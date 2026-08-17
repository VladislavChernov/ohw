"""Этот пакет служит фасадом (Facade) для всех обработчиков данных. 
Он экспортирует все функции, необходимые для координации загрузки файлов."""

from .text_processor import process_all_files as process_txt
from .pdf_processor import process_all_files as process_pdf
from .docx_processor import process_all_files as process_docx

# Словарь, который связывает расширение с соответствующей функцией-обработчиком.
PROCESSOR_MAP = {
    ".txt": process_txt,
    ".pdf": process_pdf,
    ".docx": process_docx
}
