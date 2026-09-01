# services/sentiment_service.py
import os
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from typing import List, Dict, Optional


class SentimentAnalyzer:
    def __init__(self, model_name: str = "cointegrated/rubert-tiny2"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f" [SentimentService] Инициализация модели '{model_name}' на устройстве: {self.device}")

        try:
            # Загрузка токенизатора и модели
            # Кэш по умолчанию сохраняется в ~/.cache/huggingface/
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)

            self.model.to(self.device)
            self.model.eval()  #

            self.id2label = self.model.config.id2label
            print(f" [SentimentService] Модель успешно загружена. Лейблы: {self.id2label}")
        except Exception as e:
            print(f" [SentimentService] Ошибка загрузки модели: {e}")
            raise e

    def predict_batch(self, texts: List[str], batch_size: int = 32) -> List[Dict[str, any]]:
        """Пакетная обработка текстов."""
        if not texts:
            return []

        results = []
        # Защита от пустых строк
        safe_texts = [t if isinstance(t, str) and t.strip() else "." for t in texts]

        for i in range(0, len(safe_texts), batch_size):
            batch_texts = safe_texts[i:i + batch_size]

            inputs = self.tokenizer(
                batch_texts,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True
            )
            # Перенос входных данных на GPU/CPU
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
                probs = torch.nn.functional.softmax(logits, dim=-1)

                for j in range(len(batch_texts)):
                    pred_id = probs[j].argmax().item()
                    conf = probs[j][pred_id].item()
                    label = self.id2label.get(pred_id, f"CLASS_{pred_id}")

                    results.append({
                        "label": label,
                        "confidence": round(conf, 4)
                    })
        return results


# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
import threading

_analyzer_instance = None
_analyzer_lock = threading.Lock()

def get_analyzer():
    """Потокобезопасное получение экземпляра анализатора (Double-Check Locking)"""
    global _analyzer_instance
    
    if _analyzer_instance is None:
        with _analyzer_lock:
            if _analyzer_instance is None:
                try:
                    _analyzer_instance = SentimentAnalyzer(
                        model_name='blanchefort/rubert-base-cased-sentiment-rusentiment'
                    )

                except Exception as e:
                    print(f" [SentimentService] Не удалось инициализировать модель при старте: {e}")
                    _analyzer_instance = None
    
    return _analyzer_instance


ModelTypes = ['blanchefort/rubert-base-cased-sentiment-rusentiment', "cointegrated/rubert-tiny2"]