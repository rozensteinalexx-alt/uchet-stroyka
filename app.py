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
API_KEY = "AIzaSyCPm3R27R93WGid1jfVx22LAJoBvYMpM5c" # Твой ключ
JSON_FILE = 'service_account.json'
SHEET_NAME = "Materials 2026"

# Список твоих объектов (они станут названиями листов!)
OBJECTS = ["Квартира Центр", "Дом Загород", "Офис", "Склад", "Новый Объект"]

genai.configure(api_key=API_KEY)

# ==========================================
# 2. ФУНКЦИИ
# ==========================================
def get_best_model():
    """Ищет рабочую модель"""
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in models:
            if 'flash' in m and 'lite' not in m: return m
        for m in models:
            if 'flash' in m: return m
        return models[0]
    except: return "models/gemini-1.5-flash"

def process_invoice(uploaded_file):
    """Распознаем товары И ДАТУ накладной"""
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    while myfile.state.name == "PROCESSING":
        time.sleep(1)
        myfile = genai.get_file(myfile.name)

    model = genai.GenerativeModel(get_best_model())
    
    # Промпт теперь просит найти дату документа
    prompt = """
    Analyze this invoice.
    1. Extract the **Invoice Date** (Дата документа). Format: DD.MM.YYYY. If not found, use today's date.
    2. Extract items to JSON list.
    
    Output format: JSON object with two keys:
    {
        "invoice_date": "DD.MM.YYYY",
        "items": [
            {
                "name": "Item Name (Russian)",
                "quantity": 1.0,
                "unit": "шт",
                "price": 100.0,
                "total": 100.0,
                "category": "Choose from: [Инструмент, Сухие смеси, Краски, Сантехника, Электрика, Спецодежда, Крепеж, Гипсокартон, Разное]"
            }
        ]
    }
    Return ONLY valid JSON.
    """
    
    try:
        response = model.generate_content([myfile, prompt])
        genai.delete_file(myfile.name)
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        st.error(f"Ошибка AI: {e}")
        return None

def save_to_sheet_sorted(df):
    """Пишет данные на РАЗНЫЕ листы и сортирует их"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        # Группируем данные по Объектам (чтобы писать пачками)
        # Например: 3 строки на "Офис", 2 строки на "Склад"
        for obj_name, group in df.groupby("object"):
            
            # 1. Пытаемся открыть лист с именем объекта. Если нет - создаем.
            try:
                worksheet = spreadsheet.worksheet(obj_name)
            except:
                worksheet = spreadsheet.add_worksheet(title=obj_name, rows=100, cols=10)
                # Создаем заголовки для нового листа
                worksheet.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
                worksheet.format('A1:G1', {'textFormat': {'bold': True}})

            # 2. Готовим данные
            data_rows = []
            for _, row in group.iterrows():
                # Конвертируем дату в формат Гугл Таблиц, чтобы сортировка работала корректно
                data_rows.append([
                    row['date'], # Дата из чека
                    row['name'],
                    row['quantity'],
                    row['unit'],
                    row['price'],
                    row['total'],
                    row['category']
                ])
            
            # 3. Записываем
            worksheet.append_rows(data_rows)
            
            # 4. СОРТИРОВКА (По колонке А - Дата)
            # sort_range требует указать диапазон. Берем с A2 (без заголовка) до G1000
            # Сортируем по 1-й колонке (Дата), ascending=True (от старых к новым)
            last_row = len(worksheet.get_all_values())
            if last_row > 1:
                worksheet.sort((1, 'asc'), range=f'A2:G{last_row}')
                
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

# ==========================================
# 3. ИНТЕРФЕЙС
# ==========================================
st.set_page_config(page_title="Scanner Pro", page_icon="🏗️")
st.title("🏗️ Учет 2.0: Объекты и Даты")

upl = st.file_uploader("Загрузи фото", type=['jpg', 'png', 'jpeg'])

if upl and st.button("🚀 РАСПОЗНАТЬ"):
    with st.spinner("Ищу дату и товары..."):
        result = process_invoice(upl)
        
        if result and 'items' in result:
            items = result['items']
            inv_date = result.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            
            df = pd.DataFrame(items)
            
            # Чистим от услуг
            stop_words = ['доставка', 'перевозка', 'услуга', 'разгрузка']
            df = df[~df['name'].str.contains('|'.join(stop_words), case=False, na=False)]
            
            # Добавляем колонки для редактора
            df['object'] = OBJECTS[0]
            df['date'] = inv_date # Ставим найденную дату
            
            st.session_state['df'] = df
        else:
            st.error("Не удалось прочитать накладную.")

if 'df' in st.session_state:
    st.info("💡 Проверь дату и распредели по объектам. Программа сама создаст нужные листы.")
    
    # Настройка редактора
    edited = st.data_editor(
        st.session_state['df'],
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "date": st.column_config.TextColumn("Дата документа 📅"),
            "name": st.column_config.TextColumn("Название", width="medium"),
            "category": st.column_config.SelectboxColumn("Категория", options=[
                "Инструмент", "Сухие смеси", "Краски", "Сантехника", "Электрика", "Спецодежда", "Крепеж", "Гипсокартон", "Разное"
            ]),
            "object": st.column_config.SelectboxColumn("👉 ЛИСТ (ОБЪЕКТ)", options=OBJECTS, required=True),
            "price": st.column_config.NumberColumn("Цена"),
            "total": st.column_config.NumberColumn("Сумма"),
        }
    )
    
    if st.button("💾 РАЗНЕСТИ ПО ЛИСТАМ"):
        with st.spinner("Создаю листы и сортирую по датам..."):
            if save_to_sheet_sorted(edited):
                st.success("✅ Готово! Данные разнесены по вкладкам и отсортированы.")
                time.sleep(2)
                del st.session_state['df']
                st.rerun()