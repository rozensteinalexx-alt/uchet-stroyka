import streamlit as st
import pandas as pd
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time
from datetime import datetime
import tempfile

# ==========================================
# 1. НАСТРОЙКИ
# ==========================================
st.set_page_config(page_title="Учет Стройки (Auto)", page_icon="🏗️", layout="wide")

try:
    API_KEY = st.secrets["general"]["gemini_api_key"]
    SHEET_NAME = st.secrets["general"]["sheet_name"]
    google_creds_dict = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"🚨 Ошибка настройки ключей: {e}")
    st.stop()

genai.configure(api_key=API_KEY)

CATEGORIES = [
    "Инструмент", "Сухие смеси", "Краски", "Сантехника", 
    "Электрика", "Спецодежда", "Крепеж", "Гипсокартон", "Расходники", "Разное"
]

# ==========================================
# 2. УМНЫЙ ПОИСК МОДЕЛИ (САМОЛЕЧЕНИЕ)
# ==========================================
@st.cache_resource
def get_working_model_name():
    """Спрашивает у Google доступные модели и выбирает рабочую"""
    try:
        # Получаем список всех моделей, доступных твоему ключу
        models = list(genai.list_models())
        
        # Показываем пользователю (тебе), что видит ключ
        model_names = [m.name for m in models]
        # st.write(f"🔧 (Тех. инфо) Доступные модели: {model_names}") 
        
        # 1. Ищем Flash (она быстрая)
        for m in models:
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                return m.name
        
        # 2. Если нет Flash, ищем Pro
        for m in models:
            if 'generateContent' in m.supported_generation_methods and 'pro' in m.name:
                return m.name
                
        # 3. Если ничего нет, берем любую, которая умеет писать текст
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                return m.name
                
        return "models/gemini-1.5-flash" # Заглушка на крайний случай
    except Exception as e:
        st.warning(f"Не удалось получить список моделей ({e}). Пробую стандартную.")
        return "gemini-1.5-flash"

# Определяем модель 1 раз при запуске
CURRENT_MODEL_NAME = get_working_model_name()

# ==========================================
# 3. ФУНКЦИИ
# ==========================================

@st.cache_data(ttl=60)
def get_existing_objects():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        return [ws.title for ws in spreadsheet.worksheets()]
    except Exception as e:
        return ["Основной объект"]

def process_invoice(uploaded_file):
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status(f"🧠 ИИ думает (Использую: {CURRENT_MODEL_NAME})...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Фото загружено, анализирую...")
        
        # ИСПОЛЬЗУЕМ НАЙДЕННУЮ МОДЕЛЬ
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        
        prompt = f"""
        Ты сметчик. Выпиши товары из чека в JSON.
        1. Date (DD.MM.YYYY).
        2. Items list.
        3. Categories: {CATEGORIES}
        
        JSON only:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "Name", "quantity": 1.0, "unit": "шт", "price": 100.0, "total": 100.0, "category": "..." }}
            ]
        }}
        """
        try:
            response = model.generate_content([myfile, prompt])
            genai.delete_file(myfile.name)
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            st.error(f"Ошибка ИИ: {e}")
            return None

def save_to_google_sheets(df):
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        for obj_name, group in df.groupby("object"):
            try:
                ws = spreadsheet.worksheet(obj_name)
            except:
                ws = spreadsheet.add_worksheet(title=obj_name, rows=1000, cols=10)
                ws.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
            
            rows = []
            for _, row in group.iterrows():
                rows.append([row['date'], row['name'], row['quantity'], row['unit'], row['price'], row['total'], row['category']])
            ws.append_rows(rows)
            
        get_existing_objects.clear()
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

# ==========================================
# 4. ИНТЕРФЕЙС
# ==========================================
if 'object_list' not in st.session_state:
    st.session_state['object_list'] = get_existing_objects()

st.title(f"🏗️ Учет Материалов")
st.caption(f"Работаю на модели: {CURRENT_MODEL_NAME}")
st.markdown("---")

with st.expander("➕ Добавить новый объект"):
    col_new1, col_new2 = st.columns([3, 1])
    new_obj_name = col_new1.text_input("Название объекта")
    if col_new2.button("Добавить"):
        if new_obj_name:
            st.session_state['object_list'].append(new_obj_name)
            st.rerun()

col1, col2 = st.columns([1, 2], gap="large")

with col1:
    upl = st.file_uploader("📸 Фото чека", type=['jpg', 'png', 'jpeg'])
    if upl and st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            default_obj = st.session_state['object_list'][0] if st.session_state['object_list'] else "Склад"
            df['object'] = default_obj
            st.session_state['df'] = df
            st.rerun()

with col2:
    if 'df' in st.session_state:
        st.info("👇 Выбери объект для всего чека:")
        col_bulk1, col_bulk2 = st.columns([2, 1])
        bulk_obj = col_bulk1.selectbox("Назначить всем:", options=st.session_state['object_list'])
        if col_bulk2.button("Применить"):
            st.session_state['df']['object'] = bulk_obj
            st.rerun()
        
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "date": "📅 Дата",
                "name": st.column_config.TextColumn("📦 Название", width="large"),
                "price": st.column_config.NumberColumn("Цена", format="%.0f ₽"),
                "total": st.column_config.NumberColumn("Сумма", format="%.0f ₽"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES),
                "object": st.column_config.SelectboxColumn("🏠 ОБЪЕКТ", options=st.session_state['object_list'], required=True),
            }
        )
        
        if st.button("💾 ЗАПИСАТЬ", type="primary", use_container_width=True):
            if save_to_google_sheets(edited_df):
                st.balloons()
                st.success("✅ Готово!")
                time.sleep(2)
                del st.session_state['df']
                st.rerun()
