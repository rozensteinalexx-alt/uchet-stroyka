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
# 1. НАСТРОЙКИ И АВТОРИЗАЦИЯ
# ==========================================
st.set_page_config(page_title="Учет Стройки Pro", page_icon="🏗️", layout="wide")

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
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

@st.cache_data(ttl=60) # Кэшируем список объектов на 60 секунд
def get_existing_objects():
    """Получает список всех листов из Гугл Таблицы"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        # Получаем названия всех вкладок
        titles = [ws.title for ws in spreadsheet.worksheets()]
        return titles
    except Exception as e:
        return ["Основной объект"] # Если ошибка, возвращаем заглушку

def process_invoice(uploaded_file):
    """Отправляет фото в ИИ"""
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status("🧠 ИИ анализирует чек...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Фото обработано, извлекаем позиции...")
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""
        Ты профессиональный сметчик. Твоя задача - перенести данные из чека в JSON.
        1. Найди дату чека (Format: DD.MM.YYYY).
        2. Извлеки каждую позицию.
        3. Присвой категорию из списка: {CATEGORIES}
        
        Верни ТОЛЬКО валидный JSON:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "Название", "quantity": 1.0, "unit": "шт", "price": 100.0, "total": 100.0, "category": "..." }}
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
    """Сохраняет данные, создавая новые листы при необходимости"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        for obj_name, group in df.groupby("object"):
            try:
                ws = spreadsheet.worksheet(obj_name)
            except:
                # Если листа нет - создаем новый
                ws = spreadsheet.add_worksheet(title=obj_name, rows=1000, cols=10)
                ws.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
            
            rows = []
            for _, row in group.iterrows():
                rows.append([
                    row['date'], row['name'], row['quantity'], row['unit'], 
                    row['price'], row['total'], row['category']
                ])
            ws.append_rows(rows)
            
        get_existing_objects.clear() # Сбрасываем кэш, чтобы новый лист появился в списке
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ (Загрузка списка объектов)
# ==========================================
if 'object_list' not in st.session_state:
    st.session_state['object_list'] = get_existing_objects()

# ==========================================
# 4. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Учет Материалов")
st.markdown("---")

# --- Блок добавления нового объекта ---
with st.expander("➕ Добавить новый объект (если его нет в списке)"):
    col_new1, col_new2 = st.columns([3, 1])
    new_obj_name = col_new1.text_input("Название (например: ЖК Ленина)")
    if col_new2.button("Добавить"):
        if new_obj_name and new_obj_name not in st.session_state['object_list']:
            st.session_state['object_list'].append(new_obj_name)
            st.success(f"Объект '{new_obj_name}' добавлен!")
            time.sleep(0.5)
            st.rerun()

col1, col2 = st.columns([1, 2], gap="large")

with col1:
    st.subheader("1. Загрузка")
    upl = st.file_uploader("📸 Фото чека", type=['jpg', 'png', 'jpeg'])
    
    if upl:
        st.image(upl, width=200)
        if st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
            res = process_invoice(upl)
            if res:
                df = pd.DataFrame(res['items'])
                df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
                # По умолчанию ставим первый объект из списка
                default_obj = st.session_state['object_list'][0] if st.session_state['object_list'] else "Склад"
                df['object'] = default_obj
                
                st.session_state['df'] = df
                st.rerun()

with col2:
    if 'df' in st.session_state:
        st.subheader("2. Проверка")
        
        # --- Массовый выбор объекта ---
        st.info("👇 Выбери объект для всего чека сразу:")
        col_bulk1, col_bulk2 = st.columns([2, 1])
        bulk_obj = col_bulk1.selectbox("Назначить всем:", options=st.session_state['object_list'])
        
        if col_bulk2.button("Применить ко всем"):
            st.session_state['df']['object'] = bulk_obj
            st.rerun()
        
        # --- Таблица ---
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
        
        st.markdown("---")
        
        if st.button("💾 ЗАПИСАТЬ В ТАБЛИЦУ", type="primary", use_container_width=True):
            if save_to_google_sheets(edited_df):
                st.balloons()
                st.success("✅ Готово! Данные разнесены.")
                time.sleep(2)
                del st.session_state['df']
                st.rerun()
