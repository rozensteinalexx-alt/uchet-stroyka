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
st.set_page_config(page_title="Учет Стройки", page_icon="🏗️", layout="wide")

try:
    API_KEY = st.secrets["general"]["gemini_api_key"]
    SHEET_NAME = st.secrets["general"]["sheet_name"]
    google_creds_dict = dict(st.secrets["gcp_service_account"])
except Exception as e:
    st.error(f"🚨 Ошибка доступа к ключам: {e}")
    st.stop()

genai.configure(api_key=API_KEY)

CATEGORIES = [
    "Инструмент", "Сухие смеси", "Краски", "Сантехника", 
    "Электрика", "Спецодежда", "Крепеж", "Гипсокартон", "Расходники", "Разное"
]

# ==========================================
# 2. ПОЛЕЗНЫЕ ФУНКЦИИ (ИИ, Таблицы, Красота)
# ==========================================

@st.cache_resource
def get_working_model_name():
    """Ищет рабочую модель (Flash или Pro)"""
    try:
        models = list(genai.list_models())
        # Приоритет Flash (быстрее), потом Pro
        for m in models:
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                return m.name
        return "gemini-1.5-pro"
    except:
        return "gemini-1.5-pro"

CURRENT_MODEL_NAME = get_working_model_name()

def get_existing_objects():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        return [ws.title for ws in spreadsheet.worksheets()]
    except:
        return ["Склад"]

def format_google_sheet(worksheet):
    """Делает красиво: рисует границы и жирный заголовок"""
    try:
        # 1. Жирный заголовок
        worksheet.format('A1:G1', {'textFormat': {'bold': True}})
        
        # 2. Рисуем сетку (границы) для всей таблицы
        # Это немного магии через API, чтобы не ставить лишние библиотеки
        body = {
            "requests": [
                {
                    "updateBorders": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": 0,
                            "startColumnIndex": 0,
                            "endColumnIndex": 7 # A-G (7 колонок)
                        },
                        "top": {"style": "SOLID", "width": 1},
                        "bottom": {"style": "SOLID", "width": 1},
                        "left": {"style": "SOLID", "width": 1},
                        "right": {"style": "SOLID", "width": 1},
                        "innerHorizontal": {"style": "SOLID", "width": 1},
                        "innerVertical": {"style": "SOLID", "width": 1},
                    }
                }
            ]
        }
        worksheet.spreadsheet.batch_update(body)
    except Exception as e:
        print(f"Не удалось навести красоту: {e}")

def process_invoice(uploaded_file):
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status(f"🧠 ИИ читает чек ({CURRENT_MODEL_NAME})...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Разбираем товары на русском...")
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        
        # ОБНОВЛЕННЫЙ ЗАПРОС: ПРОСИМ РУССКИЕ ЕДИНИЦЫ
        prompt = f"""
        Роль: Прораб. Задача: Извлечь данные из чека в JSON.
        
        Правила:
        1. Дата в формате DD.MM.YYYY.
        2. Единицы измерения (unit) строго на русском: "шт", "уп", "м", "кг", "пара", "компл".
        3. Категория из списка: {CATEGORIES}
        
        Верни JSON:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "Название товара", "quantity": 1.0, "unit": "шт", "price": 100.0, "total": 100.0, "category": "..." }}
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

def save_rows_to_sheets(df_to_save, target_object):
    """Сохраняет строки в конкретный объект"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        try:
            ws = spreadsheet.worksheet(target_object)
        except:
            ws = spreadsheet.add_worksheet(title=target_object, rows=1000, cols=10)
            ws.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
        
        rows = []
        for _, row in df_to_save.iterrows():
            rows.append([
                row['date'], row['name'], row['quantity'], row['unit'], 
                row['price'], row['total'], row['category']
            ])
        
        ws.append_rows(rows)
        
        # Наводим красоту (сетку) после записи
        format_google_sheet(ws)
        
        return True
    except Exception as e:
        st.error(f"Ошибка записи: {e}")
        return False

# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ
# ==========================================
if 'object_list' not in st.session_state:
    st.session_state['object_list'] = get_existing_objects()

# ==========================================
# 4. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Учет Материалов")

# --- Блок создания нового объекта ---
with st.expander("➕ Создать новый объект", expanded=False):
    c1, c2 = st.columns([3, 1])
    new_obj = c1.text_input("Название", placeholder="Например: ЖК Ленина")
    if c2.button("Добавить"):
        if new_obj and new_obj not in st.session_state['object_list']:
            st.session_state['object_list'].append(new_obj)
            st.success(f"Объект '{new_obj}' создан!")
            time.sleep(1)
            st.rerun()

st.divider()

col_left, col_right = st.columns([1, 2]) # Левая колонка уже, правая шире

with col_left:
    st.subheader("1. Чек")
    upl = st.file_uploader("Загрузить фото", type=['jpg', 'png', 'jpeg'])
    
    if upl and st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            # Добавляем галочку для выбора
            df.insert(0, "✅", False)
            st.session_state['df'] = df
            st.rerun()

with col_right:
    st.subheader("2. Распределение")
    
    if 'df' in st.session_state and not st.session_state['df'].empty:
        
        # --- ТАБЛИЦА (КОМПАКТНАЯ) ---
        # Мы скрываем 'price' и 'total' с экрана, но они остаются в памяти
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            height=400,
            column_order=("✅", "name", "quantity", "unit", "category", "date"), # <-- ПОРЯДОК И СПИСОК КОЛОНОК НА ЭКРАНЕ
            column_config={
                "✅": st.column_config.CheckboxColumn("Выбор", width="small"),
                "name": st.column_config.TextColumn("Название", width="large"),
                "quantity": st.column_config.NumberColumn("Кол-во", width="small"),
                "unit": st.column_config.TextColumn("Ед.", width="small"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES, width="medium"),
                "date": st.column_config.TextColumn("Дата", width="small"),
            }
        )
        st.session_state['df'] = edited_df
        
        st.markdown("---")
        
        # --- ПАНЕЛЬ УПРАВЛЕНИЯ (Снизу) ---
        st.write("👇 **Куда отправить выбранные галочкой позиции?**")
        
        action_col1, action_col2, action_col3 = st.columns([2, 1, 1])
        
        # 1. Выбор объекта
        target_obj = action_col1.selectbox("Выберите объект:", options=st.session_state['object_list'], label_visibility="collapsed")
        
        # 2. Кнопка Отправить
        if action_col2.button("🚀 ОТПРАВИТЬ", type="primary", use_container_width=True):
            # Берем только выбранные
            rows_to_send = edited_df[edited_df["✅"] == True]
            
            if rows_to_send.empty:
                st.warning("Сначала поставьте галочки ✅!")
            else:
                # Отправляем в Google Sheets
                if save_rows_to_sheets(rows_to_send, target_obj):
                    st.success(f"Уехало {len(rows_to_send)} поз. на '{target_obj}'")
                    # Удаляем отправленные из списка на экране
                    st.session_state['df'] = edited_df[edited_df["✅"] == False].reset_index(drop=True)
                    time.sleep(1)
                    st.rerun()
        
        # 3. Кнопка Разделить (Дубль)
        if action_col3.button("📑 Копия", help="Дублировать строку, чтобы разбить кол-во"):
            selected = edited_df[edited_df["✅"] == True]
            if not selected.empty:
                st.session_state['df'] = pd.concat([edited_df, selected], ignore_index=True)
                st.rerun()
            else:
                st.warning("Выберите строку галочкой")

    elif 'df' in st.session_state:
        st.success("🎉 Чек полностью обработан!")
        if st.button("Загрузить новый"):
            del st.session_state['df']
            st.rerun()
    else:
        st.info("👈 Загрузи фото слева")
