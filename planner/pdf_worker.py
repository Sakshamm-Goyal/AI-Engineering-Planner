"""Isolated PDFium worker. Input: PDF bytes on stdin. Output: bounded JSON.

A dedicated process avoids PDFium thread-safety concerns and permits an actual kill
on parser timeout. It is resource-limited on Unix, NOT a security sandbox.
"""
import base64
import io
import json
import math
import sys

import pdf_inspector
import pypdfium2 as pdfium


def inspect_pdf(data: bytes, limits: dict) -> dict:
    if b"%PDF-" not in data[:1024]:
        return {"error": "invalid_pdf", "message": "The uploaded content is not a PDF."}
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        encrypted = getattr(exc, "err_code", None) == pdfium.raw.FPDF_ERR_PASSWORD
        return {"error": "encrypted_pdf" if encrypted else "invalid_pdf",
                "message": "Password-protected PDFs are not supported. Export an unencrypted copy." if encrypted else "The PDF is malformed or cannot be opened."}
    try:
        if pdfium.raw.FPDF_GetSecurityHandlerRevision(doc) >= 0:
            return {"error": "encrypted_pdf", "message": "Encrypted PDFs are not supported, even when an empty password opens them."}
        if not 1 <= len(doc) <= limits["max_pages"]:
            return {"error": "page_limit", "message": f"Upload a PDF with 1–{limits['max_pages']} pages; this document has {len(doc)}."}
        # PDF Inspector classifies the whole document from its content streams in
        # milliseconds. A confidently text-based document must not be made slow
        # merely because it includes diagrams or table vector paths.
        try:
            inspection = pdf_inspector.process_pdf_bytes(data)
            fast_native = inspection.pdf_type == "text_based" and bool(inspection.markdown)
        except Exception:
            fast_native = False
        pages, total_chars, vision_count, rendered_bytes = [], 0, 0, 0
        for index in range(len(doc)):
            page = doc[index]
            try:
                w, h = page.get_size()
                if not all(math.isfinite(v) and 0 < v <= 20000 for v in (w, h)):
                    return {"error": "page_dimensions", "message": f"Page {index + 1} has unsupported dimensions."}
                textpage = page.get_textpage()
                try:
                    if textpage.count_chars() > limits["max_chars"]:
                        return {"error": "document_limit", "message": "The PDF exceeds the configured text limit; nothing was truncated."}
                    text = textpage.get_text_bounded().replace("\r\n", "\n").replace("\x00", "").strip()
                finally:
                    textpage.close()
                total_chars += len(text)
                if total_chars > limits["max_chars"]:
                    return {"error": "document_limit", "message": "The PDF exceeds the configured text limit; nothing was truncated."}
                image_area, paths, count = 0.0, 0, 0
                for obj in page.get_objects(max_depth=4):
                    count += 1
                    if count > 10000:
                        return {"error": "complex_pdf", "message": f"Page {index + 1} is too complex for this local parser. Simplify or split the PDF."}
                    if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                        left, bottom, right, top = obj.get_bounds()
                        image_area += max(0, right - left) * max(0, top - bottom)
                    elif obj.type == pdfium.raw.FPDF_PAGEOBJ_PATH:
                        paths += 1
                bad_chars = sum(c == "\ufffd" or (ord(c) < 32 and c not in "\n\t") for c in text)
                reasons = []
                if not fast_native and len(text) < 80:
                    reasons.append("Sparse native text; checking for scanned or visual content.")
                if not fast_native and image_area / (w * h) > 0.02:
                    reasons.append("Embedded image content needs visual review.")
                if not fast_native and paths >= 8:
                    reasons.append("Vector or table-like content needs visual review.")
                if not fast_native and bad_chars / max(len(text), 1) > 0.01:
                    reasons.append("Native text may have a broken encoding.")
                image = None
                if reasons:
                    vision_count += 1
                    if vision_count > limits["max_vision_pages"]:
                        return {"error": "vision_page_limit", "message": f"More than {limits['max_vision_pages']} pages need vision. Split the PDF or deliberately raise MAX_VISION_PAGES."}
                    bitmap = page.render(scale=min(2.5, 1800 / max(w, h)), may_draw_forms=False)
                    try:
                        pil = bitmap.to_pil()
                        try:
                            buf = io.BytesIO()
                            pil.save(buf, format="PNG")
                            image = base64.b64encode(buf.getvalue()).decode("ascii")
                            rendered_bytes += len(image)
                            if rendered_bytes > 40 * 1024 * 1024:
                                return {"error": "render_limit", "message": "The rendered PDF exceeds the memory budget. Split the document."}
                        finally:
                            pil.close()
                    finally:
                        bitmap.close()
                pages.append({"number": index + 1, "text": text, "image": image, "reasons": reasons})
            finally:
                page.close()
        return {"pages": pages}
    finally:
        doc.close()


def main() -> None:
    limits = json.loads(sys.argv[1])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (110, 110))
    except (ImportError, ValueError, OSError):
        pass  # Windows: parent still enforces wall timeout and explicit size limits.
    data = sys.stdin.buffer.read(limits["max_bytes"] + 1)
    try:
        if len(data) > limits["max_bytes"]:
            result = {"error": "file_limit", "message": "The PDF exceeds the upload size limit."}
        else:
            result = inspect_pdf(data, limits)
        sys.stdout.write(json.dumps(result, ensure_ascii=True))
    except Exception:
        # Parser internals can contain source text; never echo exception messages.
        sys.stdout.write(json.dumps({"error": "pdf_processing", "message": "The PDF could not be processed safely. Try re-exporting it."}))


if __name__ == "__main__":
    main()
