"""Модуль регулярной (еженедельной) отчетности по сертификатам.

Формирует подробные сводки и таблицы по состоянию сертификатов
в форматах Excel (.xlsx), HTML и JSON с сохранением в архив отчетов.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
import json
import logging
from pathlib import Path
from typing import Any

from certificate_analyzer.runtime.gemeni.config import GeminiRuntimeConfig

logger = logging.getLogger(__name__)


class CertificateReporter:
    """Генератор отчетов по сертификатам с поддержкой форматов Excel, HTML и JSON."""

    def __init__(self, output_dir: str | Path = "reports") -> None:
        """Инициализирует генератор отчетов.

        Args:
            output_dir: Каталог для сохранения сформированных файлов отчетов.
        """
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(
        self,
        certificates: list[dict[str, Any]],
        report_format: str = "xlsx",
        title: str = "Еженедельный отчет по сертификатам",
        timestamp: datetime | None = None,
    ) -> Path:
        """Создает файл отчета в заданном формате.

        Args:
            certificates: Список сериализованных сертификатов с полями статуса.
            report_format: Формат файла ('xlsx', 'html', 'json').
            title: Заголовок отчета.
            timestamp: Метка времени формирования (по умолчанию datetime.now()).

        Returns:
            Path: Абсолютный путь к созданному файлу отчета.

        Raises:
            ValueError: При неподдерживаемом формате отчета.
        """
        now = timestamp or datetime.now()
        date_str = now.strftime("%Y-%m-%d_%H%M%S")
        fmt = report_format.lower()

        stats = self.calculate_stats(certificates)

        if fmt == "xlsx":
            filename = f"certificate_report_{date_str}.xlsx"
            out_path = self.output_dir / filename
            self._generate_xlsx(certificates, stats, title, now, out_path)
            return out_path

        if fmt == "html":
            filename = f"certificate_report_{date_str}.html"
            out_path = self.output_dir / filename
            self._generate_html(certificates, stats, title, now, out_path)
            return out_path

        if fmt == "json":
            filename = f"certificate_report_{date_str}.json"
            out_path = self.output_dir / filename
            self._generate_json(certificates, stats, title, now, out_path)
            return out_path

        raise ValueError(f"Неподдерживаемый формат отчета: {report_format}")

    @staticmethod
    def calculate_stats(certificates: list[dict[str, Any]]) -> dict[str, int]:
        """Рассчитывает сводные количественные метрики по списку сертификатов.

        Args:
            certificates: Список сертификатов.

        Returns:
            dict[str, int]: Словарь со счетчиками (total, valid, expiring, expired, error).
        """
        stats = {
            "total": len(certificates),
            "valid": 0,
            "expiring_soon": 0,
            "expired": 0,
            "error": 0,
        }

        for c in certificates:
            status = str(c.get("status", "")).lower()
            if "expir" in status and "soon" in status or "истекает" in status:
                stats["expiring_soon"] += 1
            elif "expired" in status or "просроч" in status:
                stats["expired"] += 1
            elif "valid" in status or "действ" in status:
                stats["valid"] += 1
            elif "error" in status or "ошиб" in status:
                stats["error"] += 1
            else:
                stats["valid"] += 1

        return stats

    def _generate_xlsx(
        self,
        certificates: list[dict[str, Any]],
        stats: dict[str, int],
        title: str,
        now: datetime,
        output_file: Path,
    ) -> None:
        """Создает стилизованный Excel-отчет (.xlsx) через openpyxl.

        Args:
            certificates: Данные сертификатов.
            stats: Сводная статистика.
            title: Заголовок.
            now: Время отчета.
            output_file: Целевой файл.
        """
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            logger.error("Для экспорта в Excel требуется библиотека openpyxl: %s", exc)
            # При отсутствии openpyxl генерируем JSON
            self._generate_json(certificates, stats, title, now, output_file.with_suffix(".json"))
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Сертификаты"

        # Заголовок
        ws.append([title])
        ws.merge_cells("A1:G1")
        title_cell = ws["A1"]
        title_cell.font = Font(name="Segoe UI", size=16, bold=True, color="FFFFFF")
        title_cell.fill = PatternFill("solid", fgColor="4A69BD")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 40

        # Дата формирования
        ws.append([f"Дата формирования: {now.strftime('%d.%m.%Y %H:%M:%S')}"])
        ws.merge_cells("A2:G2")
        ws["A2"].font = Font(name="Segoe UI", size=10, italic=True)
        ws.row_dimensions[2].height = 20

        # Статистика
        ws.append([])
        ws.append([
            f"Всего: {stats['total']}",
            f"Действующих: {stats['valid']}",
            f"Истекает скоро: {stats['expiring_soon']}",
            f"Просроченных: {stats['expired']}",
            f"Ошибок: {stats['error']}",
        ])
        ws.row_dimensions[4].height = 22
        for col_idx in range(1, 6):
            c = ws.cell(row=4, column=col_idx)
            c.font = Font(name="Segoe UI", size=11, bold=True)
            c.fill = PatternFill("solid", fgColor="F1F2F6")

        ws.append([])

        # Шапка таблицы
        headers = [
            "Владелец (CN / ФИО)",
            "Издатель (Issuer)",
            "Серийный номер",
            "Действителен с",
            "Действителен по",
            "Осталось дней",
            "Статус",
        ]
        ws.append(headers)
        header_row = 6
        ws.row_dimensions[header_row].height = 28

        thin = Side(style="thin", color="CCCCCC")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx)
            cell.font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1E3799")
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Строки данных
        for c in certificates:
            status_text = str(c.get("status_text") or c.get("status", "Действует"))
            days_left = c.get("days_left", "")
            if isinstance(days_left, (int, float)):
                days_left_str = str(int(days_left))
            else:
                days_left_str = str(days_left)

            row_data = [
                str(c.get("common_name") or c.get("subject", "—")),
                str(c.get("issuer", "—")),
                str(c.get("serial_number", "—")),
                str(c.get("valid_from", "—")),
                str(c.get("valid_to", "—")),
                days_left_str,
                status_text,
            ]
            ws.append(row_data)
            current_row = ws.max_row
            ws.row_dimensions[current_row].height = 22

            # Подсветка статуса
            status_lower = status_text.lower()
            if "просроч" in status_lower or "expired" in status_lower:
                bg = "FFD2D2"  # светло-красный
            elif "истекает" in status_lower or "expiring" in status_lower:
                bg = "FFF3CD"  # светло-желтый
            else:
                bg = "D4EDDA"  # светло-зеленый

            for col_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=current_row, column=col_idx)
                cell.font = Font(name="Segoe UI", size=10)
                cell.border = border
                cell.alignment = Alignment(vertical="center")
                if col_idx in (4, 5, 6, 7):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                if col_idx == 7:
                    cell.fill = PatternFill("solid", fgColor=bg)

        # Автоподбор ширины столбцов
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 14), 50)

        wb.save(output_file)
        logger.info("Excel-отчет сохранен: %s", output_file)

    def _generate_html(
        self,
        certificates: list[dict[str, Any]],
        stats: dict[str, int],
        title: str,
        now: datetime,
        output_file: Path,
    ) -> None:
        """Создает красивый интерактивный HTML-отчет.

        Args:
            certificates: Данные.
            stats: Статистика.
            title: Заголовок.
            now: Время.
            output_file: Выходной файл.
        """
        rows_html = []
        for c in certificates:
            status_text = str(c.get("status_text") or c.get("status", "Действует"))
            status_lower = status_text.lower()
            badge_class = "badge-danger" if "просроч" in status_lower or "expired" in status_lower else (
                "badge-warning" if "истекает" in status_lower or "expiring" in status_lower else "badge-success"
            )
            rows_html.append(f"""
            <tr>
              <td><strong>{c.get('common_name') or c.get('subject', '—')}</strong></td>
              <td>{c.get('issuer', '—')}</td>
              <td><code>{c.get('serial_number', '—')}</code></td>
              <td>{c.get('valid_from', '—')}</td>
              <td>{c.get('valid_to', '—')}</td>
              <td class="text-center">{c.get('days_left', '—')}</td>
              <td class="text-center"><span class="badge {badge_class}">{status_text}</span></td>
            </tr>
            """)

        html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; color: #333; margin: 24px; }}
    .container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); padding: 24px; }}
    h1 {{ color: #1e3799; margin-top: 0; }}
    .meta {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
    .stats-cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
    .card {{ flex: 1; min-width: 140px; padding: 16px; border-radius: 6px; background: #f1f2f6; text-align: center; }}
    .card .num {{ font-size: 24px; font-weight: bold; margin-top: 4px; }}
    .card.valid .num {{ color: #28a745; }}
    .card.expiring .num {{ color: #fd7e14; }}
    .card.expired .num {{ color: #dc3545; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #dee2e6; font-size: 14px; }}
    th {{ background: #1e3799; color: #fff; text-align: left; font-weight: 600; }}
    tr:hover {{ background: #f8f9fa; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
    .badge-success {{ background: #d4edda; color: #155724; }}
    .badge-warning {{ background: #fff3cd; color: #856404; }}
    .badge-danger {{ background: #f8d7da; color: #721c24; }}
    .text-center {{ text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{title}</h1>
    <div class="meta">Дата формирования: {now.strftime('%d.%m.%Y %H:%M:%S')}</div>
    <div class="stats-cards">
      <div class="card">Всего сертификатов<div class="num">{stats['total']}</div></div>
      <div class="card valid">Действующих<div class="num">{stats['valid']}</div></div>
      <div class="card expiring">Скоро истекают<div class="num">{stats['expiring_soon']}</div></div>
      <div class="card expired">Просрочено<div class="num">{stats['expired']}</div></div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Владелец</th>
          <th>Издатель</th>
          <th>Серийный номер</th>
          <th>Действителен с</th>
          <th>Действителен по</th>
          <th class="text-center">Осталось дней</th>
          <th class="text-center">Статус</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
        output_file.write_text(html_content, encoding="utf-8")
        logger.info("HTML-отчет сохранен: %s", output_file)

    def _generate_json(
        self,
        certificates: list[dict[str, Any]],
        stats: dict[str, int],
        title: str,
        now: datetime,
        output_file: Path,
    ) -> None:
        """Создает отчет в формате JSON.

        Args:
            certificates: Данные.
            stats: Статистика.
            title: Заголовок.
            now: Время.
            output_file: Выходной файл.
        """
        payload = {
            "title": title,
            "generated_at": now.isoformat(),
            "statistics": stats,
            "certificates": certificates,
        }
        output_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("JSON-отчет сохранен: %s", output_file)

    @staticmethod
    def is_report_due(
        last_report_at: datetime | None,
        config: GeminiRuntimeConfig,
        now: datetime | None = None,
    ) -> bool:
        """Определяет, наступило ли время создания еженедельного отчета.

        Проверяет, прошло ли не менее report_interval_days с момента последнего отчета,
        либо совпадает ли текущий день недели и время с заданными в конфигурации.

        Args:
            last_report_at: Дата и время последнего успешного отчета.
            config: Конфигурация фонового процесса.
            now: Текущее время (datetime.now() по умолчанию).

        Returns:
            bool: True, если отчет необходимо сформировать прямо сейчас.
        """
        current_now = now or datetime.now()

        # Если отчет еще ни разу не создавался
        if last_report_at is None:
            return True

        # Если прошло больше времени, чем указано в интервале
        elapsed = current_now - last_report_at
        if elapsed >= timedelta(days=config.report_interval_days):
            return True

        # Проверка по расписанию: день недели и время
        target_weekday = config.report_day_of_week
        try:
            hour_str, min_str = config.report_time.split(":")
            target_time = time(int(hour_str), int(min_str))
        except (ValueError, AttributeError):
            target_time = time(9, 0)

        # Если сегодня нужный день недели и наступило время отчета, а отчет сегодня еще не формировался
        if current_now.weekday() == target_weekday and current_now.time() >= target_time:
            if last_report_at.date() < current_now.date():
                return True

        return False
