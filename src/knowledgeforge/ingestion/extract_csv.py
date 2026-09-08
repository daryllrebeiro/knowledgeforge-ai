import csv
from io import TextIOWrapper
from typing import BinaryIO

from knowledgeforge.config import get_settings


class CSVExtractionError(Exception):
    """Raised when CSV extraction fails a guard check."""


def extract_csv(file: BinaryIO) -> list[tuple[int, str]]:
    """Extract CSV rows as location/text pairs.

    Each row becomes one location with its cells joined by tabs.
    Guards against excessively long lines and unbounded total size.
    """
    settings = get_settings()
    max_line = settings.max_csv_line_bytes
    max_total = settings.max_csv_total_bytes

    wrapper = TextIOWrapper(file, encoding="utf-8")
    reader = csv.reader(wrapper)
    rows = []
    total_bytes = 0
    for index, row in enumerate(reader, start=1):
        # Skip empty rows
        if any(cell.strip() for cell in row):
            line = "\t".join(cell.strip() for cell in row)
            line_bytes = len(line.encode("utf-8"))
            if line_bytes > max_line:
                raise CSVExtractionError(
                    f"CSV line {index} length {line_bytes} bytes exceeds limit {max_line}"
                )
            total_bytes += line_bytes
            if total_bytes > max_total:
                raise CSVExtractionError(
                    f"CSV total size {total_bytes} bytes exceeds limit {max_total}"
                )
            rows.append((index, line))
    return rows