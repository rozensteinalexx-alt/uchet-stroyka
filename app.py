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
# 1. НАСТРОЙКИ (Берем из Сейфа)
# ==========================================
try:
    API_KEY = st.secrets["general"]["gemini_api_key"]
    SHEET_NAME = st.secrets["general"]["sheet_name"]
    # Создаем учетные данные из секретов
    google_creds_dict = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"🚨 Ошибка настройки ключей: {e}")
    st.stop()

genai.configure(api_key=API_KEY)

OBJECTS = ["Квартира Центр", "Дом Загород", "Офис", "Склад", "Личные расходы"]
CATEGORIES = [
    "Инструмент", "Сухие смеси", "Краски", "Сантехника", 
    "Электрика", "Спецодежда", "Крепеж", "Гипсокартон", "Расходники", "Разное"
]

st.set_page_config(page_title="Учет Стройки", page_icon="🏗️", layout="wide")

# ==========================================
# 2. ФУНКЦИИ
# ==========================================
def process_invoice(uploaded_file):
    """Отправляет фото в ИИ"""
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status("🧠 ИИ читает чек...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Чек прочитан, разбираем товары...")
        model = genai.GenerativeModel("models/gemini-1.5-flash")
        
        prompt = f"""
        Ты помощник прораба. Разбери чек.
        1. Найди дату (DD.MM.YYYY).
        2. Список товаров.
        3. Категории из списка: {CATEGORIES}
        
        Верни JSON:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "Название", "quantity": 1, "unit": "шт", "price": 100, "total": 100, "category": "..." }}
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
    """Сохраняет в таблицу используя Секреты"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        # Используем словарь из секретов вместо файла
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
        return True
    except Exception as e:
        st.error(f"Ошибка Таблиц: {e}")
        return False

# ==========================================
# 3. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Учет Материалов")

col1, col2 = st.columns([1, 2])

with col1:
    upl = st.file_uploader("📸 Фото чека", type=['jpg', 'png', 'jpeg'])
    if upl and st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            df['object'] = OBJECTS[0]
            st.session_state['df'] = df
            st.rerun()

with col2:
    if 'df' in st.session_state:
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "date": "📅 Дата",
                "name": st.column_config.TextColumn("📦 Название", width="large"),
                "price": st.column_config.NumberColumn("Цена ₽", format="%.0f ₽"),
                "total": st.column_config.NumberColumn("Сумма ₽", format="%.0f ₽"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES),
                "object": st.column_config.SelectboxColumn("🏠 ОБЪЕКТ", options=OBJECTS),
            }
        )
        
        if st.button("💾 ЗАПИСАТЬ В ТАБЛИЦУ", type="primary", use_container_width=True):
            if save_to_google_sheets(edited_df):
                st.balloons()
                st.success("✅ Успешно сохранено!")
                time.sleep(2)
                del st.session_state['df']
                st.rerun()
