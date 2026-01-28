import streamlit as st
import pandas as pd
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import time
from datetime import datetime
import tempfile
import os

# ==========================================
# 1. НАСТРОЙКИ (МОЖЕШЬ МЕНЯТЬ)
# ==========================================
# Вставь сюда свой API ключ Gemini
API_KEY = "AIzaSyCPm3R27R93WGid1jfVx22LAJoBvYMpM5c" 

# Имя твоей Гугл Таблицы (должно совпадать точь-в-точь)
SHEET_NAME = "Materials 2026"

# Список твоих объектов (они будут в выпадающем списке)
OBJECTS = ["Квартира Центр", "Дом Загород", "Офис", "Склад", "Личные расходы"]

# Категории для материалов
CATEGORIES = [
    "Инструмент", "Сухие смеси", "Краски", "Сантехника", 
    "Электрика", "Спецодежда", "Крепеж", "Гипсокартон", "Расходники", "Разное"
]

# ==========================================
# 2. НАСТРОЙКА СТРАНИЦЫ
# ==========================================
st.set_page_config(page_title="Учет Стройки", page_icon="🏗️", layout="wide")
genai.configure(api_key=API_KEY)

# Скрываем меню разработчика и футер Streamlit для красоты
hide_menu_style = """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)

# ==========================================
# 3. ФУНКЦИИ (МОЗГИ ПРОГРАММЫ)
# ==========================================

def get_best_model():
    """Выбирает самую быструю и дешевую модель Gemini"""
    return "models/gemini-1.5-flash"

def process_invoice(uploaded_file):
    """Отправляет фото в ИИ и получает список товаров"""
    # Сохраняем файл временно
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    tfile.write(uploaded_file.getvalue())
    tfile.close()
    
    myfile = genai.upload_file(tfile.name)
    
    # Ждем пока Гугл обработает файл
    with st.status("🧠 Искусственный интеллект думает...", expanded=True) as status:
        while myfile.state.name == "PROCESSING":
            time.sleep(1)
            myfile = genai.get_file(myfile.name)
        
        status.write("✅ Фото обработано, читаем текст...")
        model = genai.GenerativeModel(get_best_model())
        
        # Инструкция для ИИ
        prompt = f"""
        Ты помощник прораба. Посмотри на этот чек/накладную.
        1. Найди ДАТУ документа. Если не нашел - используй сегодняшнюю. Формат: DD.MM.YYYY
        2. Выпиши все купленные позиции.
        3. Для каждой позиции определи категорию из списка: {CATEGORIES}
        
        Верни ТОЛЬКО чистый JSON (без слова json и кавычек ```):
        {{
            "invoice_date": "DD.MM.YYYY",
            "items": [
                {{
                    "name": "Название товара (коротко и ясно)",
                    "quantity": 1.0,
                    "unit": "шт/кг/м/упак",
                    "price": 100.0,
                    "total": 100.0,
                    "category": "Категория из списка"
                }}
            ]
        }}
        """
        
        try:
            response = model.generate_content([myfile, prompt])
            genai.delete_file(myfile.name) # Удаляем файл с серверов Гугла
            
            # Чистим ответ от лишнего мусора
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            st.error(f"Ошибка при чтении: {e}")
            return None

def save_to_google_sheets(df):
    """Записывает данные в таблицу"""
    try:
        scope = ['[https://spreadsheets.google.com/feeds](https://spreadsheets.google.com/feeds)', '[https://www.googleapis.com/auth/drive](https://www.googleapis.com/auth/drive)']
        
        # Проверяем, где лежит ключ (в секретах Streamlit или в файле)
        if os.path.exists('service_account.json'):
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        else:
            st.error("❌ Не найден файл ключа service_account.json!")
            return False

        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        # Группируем по объектам (чтобы не открывать лист 100 раз)
        for obj_name, group in df.groupby("object"):
            try:
                worksheet = spreadsheet.worksheet(obj_name)
            except:
                # Если листа нет - создаем новый
                worksheet = spreadsheet.add_worksheet(title=obj_name, rows=1000, cols=10)
                worksheet.append_row(["Дата", "Название", "Кол-во", "Ед.", "Цена", "Сумма", "Категория"])
                worksheet.format('A1:G1', {'textFormat': {'bold': True}})

            # Готовим строки для записи
            rows_to_add = []
            for _, row in group.iterrows():
                rows_to_add.append([
                    row['date'],
                    row['name'],
                    row['quantity'],
                    row['unit'],
                    row['price'],
                    row['total'],
                    row['category']
                ])
            
            # Добавляем в конец таблицы
            worksheet.append_rows(rows_to_add)
            
        return True
    except Exception as e:
        st.error(f"❌ Ошибка записи в таблицу: {e}")
        st.info("💡 Проверь: 1. Название таблицы верное? 2. Обновил ли ты service_account.json?")
        return False

# ==========================================
# 4. ИНТЕРФЕЙС
# ==========================================
st.title("🏗️ Сканер Накладных")
st.write("Загрузи фото чека, проверь цены и нажми кнопку сохранить.")

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("1. Загрузка")
    upl = st.file_uploader("📸 Сделай фото или выбери файл", type=['jpg', 'png', 'jpeg'])
    
    if upl:
        st.image(upl, caption="Твое фото", use_container_width=True)
        if st.button("🚀 РАСПОЗНАТЬ ЧЕК", type="primary", use_container_width=True):
            res = process_invoice(upl)
            if res:
                df = pd.DataFrame(res['items'])
                df['date'] = res.get('invoice_date', datetime.now().strftime("%d.%m.%Y"))
                df['object'] = OBJECTS[0] # Выбираем первый объект по умолчанию
                st.session_state['df'] = df
                st.rerun()

with col2:
    if 'df' in st.session_state:
        st.subheader("2. Проверка данных")
        
        # Редактор таблицы
        edited_df = st.data_editor(
            st.session_state['df'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "date": st.column_config.TextColumn("📅 Дата"),
                "name": st.column_config.TextColumn("📦 Название", width="large"),
                "quantity": st.column_config.NumberColumn("Кол-во"),
                "unit": st.column_config.TextColumn("Ед."),
                "price": st.column_config.NumberColumn("Цена ₽", format="%.2f ₽"),
                "total": st.column_config.NumberColumn("Сумма ₽", format="%.2f ₽"),
                "category": st.column_config.SelectboxColumn("Категория", options=CATEGORIES, required=True),
                "object": st.column_config.SelectboxColumn("🏠 ОБЪЕКТ (Куда записать?)", options=OBJECTS, required=True),
            }
        )

        st.divider()
        
        # Большая кнопка сохранения
        btn_col1, btn_col2 = st.columns([3, 1])
        with btn_col1:
            if st.button("💾 ЗАПИСАТЬ В GOOGLE ТАБЛИЦУ", type="primary", use_container_width=True):
                with st.spinner("Записываем..."):
                    if save_to_google_sheets(edited_df):
                        st.balloons()
                        st.success(f"✅ Успешно добавлено {len(edited_df)} позиций!")
                        time.sleep(3)
                        del st.session_state['df']
                        st.rerun()
        with btn_col2:
            if st.button("❌ Сброс"):
                del st.session_state['df']
                st.rerun()

    else:
        st.info("👈 Загрузи фото слева, чтобы начать работу.")