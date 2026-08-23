from io import BytesIO

import psycopg
from google import genai

from knowledgeforge.config import Settings
from knowledgeforge.ingestion.chunk import chunk_pages
from knowledgeforge.ingestion.embed import embed_texts
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_docx import extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.ingestion.store import store_chunks
from knowledgeforge.worker.cloud import CloudStorageClient


def process_ingestion_job(job: IngestionJob, settings: Settings) -> None:
    storage = CloudStorageClient(settings.gcs_bucket)
    content = storage.download(job.storage_uri)
    filename = job.storage_uri.rsplit("/", 1)[-1].lower()
    if filename.endswith(".pdf"):
        pages = extract_pdf(BytesIO(content))
    elif filename.endswith(".docx"):
        pages = extract_docx(BytesIO(content))
    else:
        pages = extract_markdown(BytesIO(content))
    chunks = chunk_pages(pages, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
    client = genai.Client(api_key=settings.gemini_api_key)
    embeddings = embed_texts(
        client, [chunk.text for chunk in chunks], model=settings.gemini_embedding_model
    )
    with psycopg.connect(settings.database_url) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM chunks WHERE document_id = %s", (job.document_id,))
        store_chunks(connection, job.document_id, chunks, embeddings)
