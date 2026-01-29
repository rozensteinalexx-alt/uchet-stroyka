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
# 2. УМНЫЙ ПОИСК МОДЕЛИ
# ==========================================
@st.cache_resource
def get_working_model_name():
    """Ищет рабочую модель Gemini"""
    try:
        models = list(genai.list_models())
        for m in models: # Ищем Flash (быстрая)
            if 'generateContent' in m.supported_generation_methods and 'flash' in m.name:
                return m.name
        for m in models: # Ищем Pro (умная)
            if 'generateContent' in m.supported_generation_methods and 'pro' in m.name:
                return m.name
        return "gemini-1.5-pro" # Заглушка
    except:
        return "gemini-1.5-pro"

CURRENT_MODEL_NAME = get_working_model_name()

# ==========================================
# 3. ФУНКЦИИ
# ==========================================
def get_existing_objects():
    """Получает список вкладок из таблицы"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        titles = [ws.title for ws in spreadsheet.worksheets()]
        # Убираем служебные листы, если нужно
        return titles
    except Exception as e:
        return ["Создай объект"]

def process_invoice(uploaded_file):
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    with st.status(f"🧠 Читаем чек ({CURRENT_MODEL_NAME})...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Разбираем товары...")
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        
        prompt = f"""
        Extract items from invoice to JSON.
        1. Date (DD.MM.YYYY)
        2. Items
        3. Category from: {CATEGORIES}
        
        JSON structure:
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{ "name": "Item Name", "quantity": 1.0, "unit": "pcs", "price": 100.0, "total": 100.0, "category": "..." }}
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

def save_rows_to_sheets(df_to_save):
    """Сохраняет только переданные строки"""
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(google_creds_dict, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        # Группируем по объектам и сохраняем
        for obj_name, group in df_to_save.groupby("object"):
            # Пропускаем, если объект не выбран
            if not obj_name or obj_name == "Выбери объект...":
                continue
                
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
        st.error(f"Ошибка записи: {e}")
        return False

# ==========================================
# 4. ИНИЦИАЛИЗАЦИЯ
# ==========================================
if 'object_list' not in st.session_state:
    st.session_state['object_list'] = get_existing_objects()

# Если объектов нет вообще
if not st.session_state['object_list']:
    st.session_state['object_list'] = ["Склад"]

# ==========================================
# 5. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Учет Материалов")

# --- ВЕРХНЕЕ МЕНЮ: Управление объектами ---
with st.expander("⚙️ Управление объектами (Создать / Выбрать)", expanded=True):
    c1, c2, c3 = st.columns([2, 2, 1])
    
    # 1. Добавить новый
    new_obj = c1.text_input("Создать новый объект:", placeholder="Например: ЖК Ленина")
    if c1.button("➕ Создать"):
        if new_obj and new_obj not in st.session_state['object_list']:
            st.session_state['object_list'].append(new_obj)
            st.success(f"Объект '{new_obj}' создан!")
            st.rerun()
            
    # 2. Массовый выбор
    bulk_obj = c2.selectbox("Назначить один объект для ВСЕХ позиций:", ["-"] + st.session_state['object_list'])
    if c2.button("Применить ко всем"):
        if 'df' in st.session_state and bulk_obj != "-":
            st.session_state['df']['object'] = bulk_obj
            st.rerun()

st.divider()

# --- ОСНОВНАЯ ЗОНА ---
col_left, col_right = st.columns([1, 3])

with col_left:
    st.subheader("1. Загрузка")
    upl = st.file_uploader("Фото чека", type=['jpg', 'png', 'jpeg'])
    if upl and st.button("🚀 РАСПОЗНАТЬ"):
        res = process_invoice(upl)
        if res:
            df = pd.DataFrame(res['items'])
            df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
            # Добавляем колонку для галочки (выбор)
            df.insert(0, "✅", False)
            # Колонку объекта оставляем пустой или ставим дефолт
            df['object'] = bulk_obj if bulk_obj != "-" else "Выбери объект..."
            
            st.session_state['df'] = df
            st.rerun()

with col_right:
    st.subheader("2. Распределение")
    
    if 'df' in st.session_state and not st.session_state['df'].empty:
        
        # Редактор таблицы
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            height=400,
            column_config={
                "✅": st.column_config.CheckboxColumn("Выбрать", width="small"),
                "date": st.column_config.TextColumn("Дата", width="small"),
                "name": st.column_config.TextColumn("Название", width="large"),
                "quantity": st.column_config.NumberColumn("Кол-во", width="small"),
                "price": st.column_config.NumberColumn("Цена", format="%.0f ₽"),
                "total": st.column_config.NumberColumn("Сумма", format="%.0f ₽"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES),
                "object": st.column_config.SelectboxColumn("🏠 Куда отправить?", options=st.session_state['object_list'], required=True),
            }
        )
        
        # Обновляем состояние (чтобы запомнить галочки и изменения)
        st.session_state['df'] = edited_df
        
        # --- КНОПКИ ДЕЙСТВИЙ ---
        b1, b2, b3 = st.columns(3)
        
        # Кнопка СПЛИТ (Дублирование)
        if b1.button("📑 Копировать выбранные"):
            # Берем строки, где стоит галочка
            selected_rows = edited_df[edited_df["✅"] == True]
            if not selected_rows.empty:
                # Дублируем их и добавляем в конец
                st.session_state['df'] = pd.concat([edited_df, selected_rows], ignore_index=True)
                st.rerun()
            else:
                st.warning("Сначала поставь галочку ✅ у товара, который хочешь разделить!")

        # Кнопка ОТПРАВИТЬ (Записать и Удалить)
        if b3.button("🚀 ОТПРАВИТЬ ВЫБРАННЫЕ", type="primary"):
            # Берем выбранные строки
            rows_to_send = edited_df[edited_df["✅"] == True]
            
            if rows_to_send.empty:
                st.warning("Ничего не выбрано! Поставь галочки ✅.")
            else:
                # Проверка: выбран ли объект
                if "Выбери объект..." in rows_to_send['object'].values:
                    st.error("⚠️ У одной из позиций не выбран Обьект! Укажи куда везти.")
                else:
                    if save_rows_to_sheets(rows_to_send):
                        st.success(f"✅ Уехало позиций: {len(rows_to_send)}")
                        # УДАЛЯЕМ ОТПРАВЛЕННЫЕ ИЗ ТАБЛИЦЫ
                        st.session_state['df'] = edited_df[edited_df["✅"] == False].reset_index(drop=True)
                        time.sleep(1)
                        st.rerun()

    elif 'df' in st.session_state and st.session_state['df'].empty:
        st.info("🎉 Список пуст! Все чеки обработаны.")
        if st.button("Начать заново"):
            del st.session_state['df']
            st.rerun()
    else:
        st.info("👈 Загрузи чек слева.")
