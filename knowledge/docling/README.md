# Docling

[Docling](https://github.com/DS4SD/docling) is a powerful, flexible open source document processing library that converts various document formats into a unified format. It has advanced document understanding capabilities powered by state-of-the-art AI models for layout analysis and table structure recognition.

The whole system runs locally on standard computers and is designed to be extensible - developers can add new models or modify the pipeline for specific needs. It's particularly useful for tasks like enterprise document search, passage retrieval, and knowledge extraction. With its advanced chunking and processing capabilities, it's the perfect tool for providing GenAI applications with knowledge through RAG (Retrieval Augmented Generation) pipelines.

## Key Features

- **Universal Format Support**: Process PDF, DOCX, XLSX, PPTX, Markdown, HTML, images, and more
- **Advanced Understanding**: AI-powered layout analysis and table structure recognition
- **Flexible Output**: Export to HTML, Markdown, JSON, or plain text
- **High Performance**: Efficient processing on local hardware

## Quick Start

### Installation

```bash
pip install docling
```

### Basic Usage

```python
from docling.document_converter import DocumentConverter

# Initialize converter
converter = DocumentConverter()

# Convert a single document
result = converter.convert_single("https://arxiv.org/pdf/1706.03762")

# Export to different formats
markdown_output = result.export_to_markdown()
json_output = result.export_to_dict()
```

## Document Processing

### Supported Input Formats

| Format | Description |
|--------|-------------|
| PDF | Native PDF documents with layout preservation |
| DOCX, XLSX, PPTX | Microsoft Office formats (2007+) |
| Markdown | Plain text with markup |
| HTML/XHTML | Web documents |
| Images | PNG, JPEG, TIFF, BMP |
| USPTO XML | Patent documents |
| PMC XML | PubMed Central articles |

### Processing Pipeline

The standard pipeline includes:

1. Document parsing with format-specific backend
2. Layout analysis using AI models
3. Table structure recognition
4. Metadata extraction
5. Content organization and structuring
6. Export formatting

## Models

Docling leverages two primary specialized AI models for document understanding. At its core, the layout analysis model is built on the `RT-DETR (Real-Time Detection Transformer)` architecture, which excels at detecting and classifying page elements. This model processes pages at 72 dpi resolution and can analyze a single page in under a second on a standard CPU, having been trained on the comprehensive `DocLayNet` dataset.

The second key model is `TableFormer`, a table structure recognition system that can handle complex table layouts including partial borders, empty cells, spanning cells, and hierarchical headers. TableFormer typically processes tables in 2-6 seconds on CPU, making it efficient for practical use. 

For documents requiring text extraction from images, Docling integrates `EasyOCR` as an optional component, which operates at 216 dpi for optimal quality but requires about 30 seconds per page. Both the layout analysis and TableFormer models were developed by IBM Research and are publicly available as pre-trained weights on Hugging Face under "ds4sd/docling-models".

For more detailed information about these models and their implementation, you can refer to the [technical documentation](arxiv.org/pdf/2408.09869).

## Documentation

For full documentation, visit [documentation site](https://docling.readthedocs.io/).

For example notebooks and more detailed guides, check out [GitHub repository](https://github.com/organization/docling).