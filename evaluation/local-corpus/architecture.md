# Architecture

KnowledgeForge AI is a tenant-isolated retrieval-augmented generation service. It
extracts documents, splits them into chunks, embeds the chunks, retrieves relevant
passages, and asks Gemini to produce grounded answers with page citations.
