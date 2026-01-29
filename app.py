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
    """Рисует сетку в таблице"""
    try:
        ws.format('A1:G1', {'textFormat': {'bold': True}})
        body = {
            "requests": [{"updateBorders": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": 7},
                "top": {"style": "SOLID", "width": 1}, "bottom": {"style": "SOLID", "width": 1},
                "left": {"style": "SOLID", "width": 1}, "right": {"style": "SOLID", "width": 1},
                "innerHorizontal": {"style": "SOLID", "width": 1}, "innerVertical": {"style": "SOLID", "width": 1},
            }}]
        }
        ws.spreadsheet.batch_update(body)
    except:
        pass

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
        Роль: Сметчик.
        Задача: Извлечь данные из чека.
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

def save_single_row(row_data, target_obj, actual_qty):
    """Сохраняет одну строку с указанным количеством"""
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
        
        # Пересчитываем сумму пропорционально количеству
        price_per_unit = row_data['price']
        new_total = price_per_unit * actual_qty
        
        ws.append_row([
            row_data['date'], row_data['name'], actual_qty, row_data['unit'], 
            row_data['price'], new_total, row_data['category']
        ])
        format_google_sheet(ws)
        return True
    except Exception as e:
        st.error(f"Ошибка сохранения: {e}")
        return False

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

# --- БЛОК 1: Создание объекта ---
with st.expander("➕ Новый объект"):
    c1, c2 = st.columns([3, 1])
    new_obj = c1.text_input("Имя объекта", placeholder="Например: ЖК Ленина")
    if c2.button("Создать"):
        if new_obj and new_obj not in st.session_state['object_list']:
            st.session_state['object_list'].append(new_obj)
            st.rerun()

st.divider()

col_left, col_right = st.columns([1, 2])

# --- БЛОК 2: Загрузка чека ---
with col_left:
    st.subheader("1. Загрузка")
    upl = st.file_uploader("Фото накладной", type=['jpg', 'png', 'jpeg'])
    
    if upl and st.button("🚀 РАСПОЗНАТЬ", type="primary", use_container_width=True):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            # Добавляем ID чтобы различать строки
            df['id'] = range(1, len(df) + 1)
            # Колонка выбора (галочка)
            df.insert(0, "select", False)
            st.session_state['df'] = df
            st.rerun()

# --- БЛОК 3: Работа с товарами ---
with col_right:
    st.subheader("2. Распределение")
    
    if not st.session_state['df'].empty:
        
        # 1. ТАБЛИЦА (Редактируемая)
        # Важно: используем key, чтобы состояние не слетало
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            height=350,
            column_order=("select", "name", "quantity", "unit", "category", "date"),
            column_config={
                "select": st.column_config.CheckboxColumn("✅", width="small"),
                "name": st.column_config.TextColumn("Название", width="large", disabled=True),
                "quantity": st.column_config.NumberColumn("Остаток", width="small", disabled=True),
                "unit": st.column_config.TextColumn("Ед.", width="small"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES),
                "date": st.column_config.TextColumn("Дата", width="small"),
            },
            key="editor" 
        )
        
        # Обновляем сессию при изменении галочек, но аккуратно
        # Мы используем это состояние для логики ниже
        
        # 2. АНАЛИЗ ВЫБОРА
        selected_rows = edited_df[edited_df["select"] == True]
        count_selected = len(selected_rows)
        
        st.markdown("---")
        
        # 3. ПАНЕЛЬ ДЕЙСТВИЙ (Умная)
        if count_selected == 0:
            st.info("👈 Выбери галочкой товар, который хочешь отправить.")
            
        elif count_selected == 1:
            # --- РЕЖИМ "РАЗДЕЛИТЕЛЬ" (СЛАЙДЕР) ---
            row = selected_rows.iloc[0] # Берем единственную выбранную строку
            max_qty = float(row['quantity'])
            
            st.write(f"📦 **{row['name']}** (Всего: {max_qty} {row['unit']})")
            
            act_col1, act_col2, act_col3 = st.columns([1, 2, 1])
            
            # Слайдер (или ввод числа)
            send_qty = act_col1.number_input("Сколько отправить?", min_value=0.1, max_value=max_qty, value=max_qty, step=1.0)
            
            # Выбор объекта
            target_obj = act_col2.selectbox("Куда?", options=st.session_state['object_list'])
            
            # Кнопка
            if act_col3.button("🚀 ОТПРАВИТЬ ЧАСТЬ", type="primary", use_container_width=True):
                # 1. Сохраняем в Гугл
                if save_single_row(row, target_obj, send_qty):
                    # 2. Вычисляем остаток
                    new_qty = max_qty - send_qty
                    
                    # 3. Обновляем таблицу в памяти
                    idx = row.name # Индекс строки
                    
                    if new_qty <= 0:
                        # Если отправили всё - удаляем строку
                        st.session_state['df'] = st.session_state['df'].drop(index=idx).reset_index(drop=True)
                    else:
                        # Если осталось - обновляем количество и снимаем галочку
                        st.session_state['df'].at[idx, 'quantity'] = new_qty
                        st.session_state['df'].at[idx, 'select'] = False
                        
                    st.success(f"Уехало {send_qty} {row['unit']} на {target_obj}")
                    time.sleep(0.5)
                    st.rerun()

        else:
            # --- РЕЖИМ "МАССОВАЯ ОТПРАВКА" (Без разделения) ---
            st.warning(f"Выбрано позиций: {count_selected}. В этом режиме товары уедут ЦЕЛИКОМ.")
            
            act_col1, act_col2 = st.columns([2, 1])
            target_obj = act_col1.selectbox("Отправить всё выбранное на:", options=st.session_state['object_list'])
            
            if act_col2.button("🚀 ОТПРАВИТЬ ВСЁ", type="primary"):
                success_count = 0
                indices_to_drop = []
                
                for idx, row in selected_rows.iterrows():
                    if save_single_row(row, target_obj, row['quantity']):
                        success_count += 1
                        indices_to_drop.append(idx)
                
                # Удаляем отправленные
                st.session_state['df'] = st.session_state['df'].drop(index=indices_to_drop).reset_index(drop=True)
                st.success(f"Отправлено позиций: {success_count}")
                time.sleep(1)
                st.rerun()

    elif 'df' in st.session_state and st.session_state['df'].empty:
        st.success("🎉 Список чист! Можно загружать следующий чек.")
        if st.button("Загрузить новый"):
            del st.session_state['df']
            st.rerun()
    else:
        st.info("👈 Загрузи чек слева.")
