"""Detection-only adapter for Box6 live capture scoring.

The final OCR stage continues to use the complete PaddleOCR engine. This
adapter reuses its already-loaded detection model and exactly its configured
detector parameters. Call predict while holding the pipeline OCR lock.
"""

import warnings


class CaptureDetector:
    """Expose PaddleOCR-compatible detection results without recognition."""

    def __init__(self, ocr_engine):
        self.ocr_engine = ocr_engine
        self._detector = None
        self._parameters = {}
        self.last_mode = "full_ocr_fallback"
        self.fallback_reason = None
        try:
            pipeline = ocr_engine.paddlex_pipeline
            # The installed auto-parallel wrapper forwards these attributes.
            # Preprocessing would change coordinates, so retain full predict
            # whenever the caller enables document preprocessing.
            if getattr(pipeline, "use_doc_preprocessor", False):
                self.fallback_reason = "document preprocessing is enabled"
            else:
                detector = pipeline.text_det_model
                parameters = pipeline.get_text_det_params()
                if callable(detector) and isinstance(parameters, dict):
                    self._detector = detector
                    self._parameters = parameters
        except (AttributeError, TypeError) as exc:
            self.fallback_reason = str(exc)

    @property
    def detection_only_available(self):
        return self._detector is not None

    def predict(self, image):
        if self._detector is not None:
            try:
                results = list(self._detector([image], **self._parameters))
                if not results or any("dt_polys" not in item for item in results):
                    raise ValueError("detector did not return dt_polys")
                self.last_mode = "detection_only"
                return results
            except Exception as exc:
                # Package/API changes should not disable capture. Retry through
                # the supported complete engine and avoid repeat fast-path errors.
                self.fallback_reason = str(exc)
                self._detector = None
                warnings.warn(
                    "Capture detector falling back to full OCR: " + str(exc),
                    RuntimeWarning,
                    stacklevel=2,
                )
        self.last_mode = "full_ocr_fallback"
        return list(self.ocr_engine.predict(image))
