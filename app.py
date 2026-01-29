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
st.set_page_config(page_title="Учет Стройки Pro", page_icon="🏗️", layout="wide")

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
# 2. ФУНКЦИИ
# ==========================================
@st.cache_resource
def get_working_model_name():
    try:
        models = list(genai.list_models())
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

def format_google_sheet(ws):
    """Рисует сетку и РАСТЯГИВАЕТ колонку с названием"""
    try:
        # Жирный заголовок
        ws.format('A1:G1', {'textFormat': {'bold': True}})
        
        body = {
            "requests": [
                # 1. Рисуем границы (сетку)
                {
                    "updateBorders": {
                        "range": {"sheetId": ws.id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": 7},
                        "top": {"style": "SOLID", "width": 1}, "bottom": {"style": "SOLID", "width": 1},
                        "left": {"style": "SOLID", "width": 1}, "right": {"style": "SOLID", "width": 1},
                        "innerHorizontal": {"style": "SOLID", "width": 1}, "innerVertical": {"style": "SOLID", "width": 1},
                    }
                },
                # 2. РАСТЯГИВАЕМ КОЛОНКУ "B" (Название) до 400 пикселей
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": 1, # Колонка B (индекс 1)
                            "endIndex": 2
                        },
                        "properties": {
                            "pixelSize": 400 # <-- ШИРИНА КОЛОНКИ
                        },
                        "fields": "pixelSize"
                    }
                }
            ]
        }
        ws.spreadsheet.batch_update(body)
    except Exception as e:
        print(f"Ошибка форматирования: {e}")

def process_invoice(uploaded_file):
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status(f"🧠 ИИ читает чек ({CURRENT_MODEL_NAME})...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Разбираем данные...")
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        
        prompt = f"""
        Роль: Сметчик. Задача: Извлечь данные из чека.
        Важно: Единицы измерения (unit) переводи на русский: "шт", "уп", "м", "кг", "компл".
        Категории: {CATEGORIES}
        
        JSON:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "...", "quantity": 10.0, "unit": "шт", "price": 100.0, "total": 1000.0, "category": "..." }}
            ]
        }}
        """
        try:
            response = model.generate_content([myfile, prompt])
            genai.delete_file(myfile.name)
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            st.error(f"Ошибка: {e}")
            return None

def save_and_update(df_full, target_obj):
    """Сохраняет выбранные строки, обновляет остатки и форматирует таблицу"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        try:
            ws = spreadsheet.worksheet(target_obj)
        except:
            ws = spreadsheet.add_worksheet(title=target_obj, rows=1000, cols=10)
            ws.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
        
        rows_to_process = df_full[df_full['select'] == True]
        
        new_df = df_full.copy()
        indices_to_drop = []
        rows_to_append = []
        
        for idx, row in rows_to_process.iterrows():
            send_qty = row['send_qty']
            actual_qty = row['quantity']
            
            if send_qty > actual_qty:
                st.error(f"Ошибка! '{row['name']}': Нельзя отправить {send_qty}, когда на складе {actual_qty}.")
                return False, df_full
            
            if send_qty <= 0:
                continue 
                
            price_per_unit = row['price']
            new_total = price_per_unit * send_qty
            
            rows_to_append.append([
                row['date'], row['name'], send_qty, row['unit'], 
                row['price'], new_total, row['category']
            ])
            
            remainder = actual_qty - send_qty
            
            if remainder <= 0.001:
                indices_to_drop.append(idx)
            else:
                new_df.at[idx, 'quantity'] = remainder 
                new_df.at[idx, 'send_qty'] = remainder 
                new_df.at[idx, 'select'] = False 
        
        if rows_to_append:
            ws.append_rows(rows_to_append)
            # ВЫЗЫВАЕМ ФУНКЦИЮ КРАСОТЫ ПОСЛЕ ЗАПИСИ
            format_google_sheet(ws)
        
        new_df = new_df.drop(index=indices_to_drop).reset_index(drop=True)
        return True, new_df
        
    except Exception as e:
        st.error(f"Ошибка сохранения: {e}")
        return False, df_full

# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ
# ==========================================
if 'object_list' not in st.session_state:
    st.session_state['object_list'] = get_existing_objects()

if 'df' not in st.session_state:
    st.session_state['df'] = pd.DataFrame()

# ==========================================
# 4. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Учет Материалов")

# --- БЛОК 1: Объекты ---
with st.expander("➕ Создать новый объект"):
    c1, c2 = st.columns([3, 1])
    new_obj = c1.text_input("Название")
    if c2.button("Добавить"):
        if new_obj and new_obj not in st.session_state['object_list']:
            st.session_state['object_list'].append(new_obj)
            st.rerun()

st.divider()

col_left, col_right = st.columns([1, 3]) 

# --- БЛОК 2: Загрузка ---
with col_left:
    st.subheader("1. Загрузка")
    upl = st.file_uploader("Фото накладной", type=['jpg', 'png', 'jpeg'])
    
    if upl and st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            df.insert(0, "select", False) 
            df['send_qty'] = df['quantity'] 
            st.session_state['df'] = df
            st.rerun()

# --- БЛОК 3: Таблица и Действия ---
with col_right:
    st.subheader("2. Распределение")
    
    if not st.session_state['df'].empty:
        
        bc1, bc2 = st.columns([1, 5])
        if bc1.button("Выбрать все"):
            st.session_state['df']['select'] = True
            st.rerun()
        if bc2.button("Снять все"):
            st.session_state['df']['select'] = False
            st.rerun()

        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            height=500,
            column_order=("select", "name", "quantity", "send_qty", "unit", "category"), 
            column_config={
                "select": st.column_config.CheckboxColumn("✅", width="small"),
                "name": st.column_config.TextColumn("Название", width="large", disabled=True),
                "quantity": st.column_config.NumberColumn("Склад", disabled=True, format="%.1f"),
                "send_qty": st.column_config.NumberColumn("📤 Отправить", min_value=0.01, step=1.0, format="%.1f"),
                "unit": st.column_config.TextColumn("Ед.", width="small"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES, width="medium"),
            }
        )
        
        st.session_state['df'] = edited_df
        
        st.markdown("---")
        
        count_selected = len(edited_df[edited_df['select'] == True])
        panel_col1, panel_col2 = st.columns([2, 1])
        
        target_obj = panel_col1.selectbox("Куда везем?", options=st.session_state['object_list'])
        
        btn_type = "primary" if count_selected > 0 else "secondary"
        btn_text = f"🚀 ОТПРАВИТЬ ({count_selected} поз.)" if count_selected > 0 else "Выберите позиции"
        
        if panel_col2.button(btn_text, type=btn_type, use_container_width=True):
            if count_selected == 0:
                st.warning("Сначала поставь галочки ✅!")
            else:
                success, updated_df = save_and_update(edited_df, target_obj)
                if success:
                    st.session_state['df'] = updated_df
                    st.balloons()
                    st.success(f"Успешно отправлено на объект '{target_obj}'!")
                    time.sleep(1)
                    st.rerun()

    elif 'df' in st.session_state and st.session_state['df'].empty:
        st.success("🎉 Чек пуст! Все товары распределены.")
        if st.button("Загрузить новый"):
            del st.session_state['df']
            st.rerun()
    else:
        st.info("👈 Загрузи чек слева.")
