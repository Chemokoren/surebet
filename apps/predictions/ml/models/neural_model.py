"""
Neural Network Match Outcome Prediction Model.

LSTM/Dense hybrid for time-series-aware match prediction.
Captures temporal patterns in team form sequences.
"""

import logging
import os
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, f1_score

logger = logging.getLogger(__name__)

OUTCOME_MAP = {'home_win': 0, 'draw': 1, 'away_win': 2}
OUTCOME_LABELS = {v: k for k, v in OUTCOME_MAP.items()}


class NeuralPredictor:
    """
    Neural Network predictor for match outcomes.

    Uses a Dense network (with optional LSTM layers for sequence data).
    Falls back to a sklearn MLPClassifier if TensorFlow is unavailable.
    """

    def __init__(self, use_tensorflow: bool = True):
        self.use_tensorflow = use_tensorflow
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names: list[str] = []
        self.training_metrics: dict = {}
        self._tf_available = False

        if use_tensorflow:
            try:
                import tensorflow as tf
                self._tf_available = True
            except ImportError:
                logger.warning("TensorFlow not available, falling back to sklearn MLP")
                self._tf_available = False

    # ── Training ────────────────────────────────

    def train(self, X: pd.DataFrame, y: pd.Series, test_size: float = 0.2) -> dict:
        """Train the neural model."""
        y_encoded = y.map(OUTCOME_MAP).values
        self.feature_names = list(X.columns)

        X_train, X_val, y_train, y_val = train_test_split(
            X.values, y_encoded, test_size=test_size, random_state=42, stratify=y_encoded,
        )

        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_val_scaled = self.scaler.transform(X_val)

        if self._tf_available:
            self._train_tensorflow(X_train_scaled, y_train, X_val_scaled, y_val)
        else:
            self._train_sklearn(X_train_scaled, y_train, X_val_scaled, y_val)

        # Evaluate
        y_prob = self._predict_proba_internal(X_val_scaled)
        y_pred = np.argmax(y_prob, axis=1)

        self.training_metrics = {
            'accuracy': round(accuracy_score(y_val, y_pred), 4),
            'log_loss': round(log_loss(y_val, y_prob), 4),
            'f1_weighted': round(f1_score(y_val, y_pred, average='weighted'), 4),
            'backend': 'tensorflow' if self._tf_available else 'sklearn_mlp',
            'samples_train': len(X_train),
            'samples_val': len(X_val),
        }

        logger.info(f"Neural model trained: {self.training_metrics}")
        return self.training_metrics

    def _train_tensorflow(self, X_train, y_train, X_val, y_val):
        """Build and train a TensorFlow/Keras model."""
        import tensorflow as tf

        n_features = X_train.shape[1]

        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(n_features,)),
            tf.keras.layers.Dense(128, activation='relu'),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dropout(0.3),
            tf.keras.layers.Dense(64, activation='relu'),
            tf.keras.layers.BatchNormalization(),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(32, activation='relu'),
            tf.keras.layers.Dense(3, activation='softmax'),
        ])

        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
            loss='sparse_categorical_crossentropy',
            metrics=['accuracy'],
        )

        model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=100,
            batch_size=64,
            verbose=0,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(
                    patience=10, restore_best_weights=True,
                ),
                tf.keras.callbacks.ReduceLROnPlateau(
                    factor=0.5, patience=5, min_lr=1e-6,
                ),
            ],
        )

        self.model = model

    def _train_sklearn(self, X_train, y_train, X_val, y_val):
        """Fallback: train an sklearn MLPClassifier."""
        from sklearn.neural_network import MLPClassifier

        self.model = MLPClassifier(
            hidden_layer_sizes=(128, 64, 32),
            activation='relu',
            solver='adam',
            max_iter=500,
            early_stopping=True,
            validation_fraction=0.15,
            random_state=42,
        )
        self.model.fit(X_train, y_train)

    # ── Prediction ──────────────────────────────

    def predict(self, features: dict) -> dict:
        """Predict match outcome probabilities from a feature dict."""
        if self.model is None:
            raise RuntimeError("Model not trained or loaded.")

        feature_vector = pd.DataFrame([features])
        for col in self.feature_names:
            if col not in feature_vector.columns:
                feature_vector[col] = 0.0
        feature_vector = feature_vector[self.feature_names].values

        scaled = self.scaler.transform(feature_vector)
        probs = self._predict_proba_internal(scaled)[0]
        predicted_class = int(np.argmax(probs))

        return {
            'home_win_prob': round(float(probs[0]), 4),
            'draw_prob': round(float(probs[1]), 4),
            'away_win_prob': round(float(probs[2]), 4),
            'predicted_outcome': OUTCOME_LABELS[predicted_class],
            'confidence': round(float(np.max(probs)) * 100, 2),
        }

    def _predict_proba_internal(self, X_scaled: np.ndarray) -> np.ndarray:
        """Get probability array from whichever backend."""
        if self._tf_available:
            return self.model.predict(X_scaled, verbose=0)
        else:
            return self.model.predict_proba(X_scaled)

    # ── Persistence ─────────────────────────────

    def save(self, filepath: str) -> None:
        """Save model and scaler."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        if self._tf_available:
            model_dir = filepath + '_tf'
            self.model.save(model_dir)
            joblib.dump({
                'scaler': self.scaler,
                'feature_names': self.feature_names,
                'metrics': self.training_metrics,
                'backend': 'tensorflow',
                'model_dir': model_dir,
            }, filepath)
        else:
            joblib.dump({
                'model': self.model,
                'scaler': self.scaler,
                'feature_names': self.feature_names,
                'metrics': self.training_metrics,
                'backend': 'sklearn_mlp',
            }, filepath)
        logger.info(f"Neural model saved to {filepath}")

    def load(self, filepath: str) -> None:
        """Load model from disk."""
        data = joblib.load(filepath)
        self.scaler = data['scaler']
        self.feature_names = data['feature_names']
        self.training_metrics = data.get('metrics', {})

        if data.get('backend') == 'tensorflow':
            import tensorflow as tf
            self._tf_available = True
            self.model = tf.keras.models.load_model(data['model_dir'])
        else:
            self._tf_available = False
            self.model = data['model']
        logger.info(f"Neural model loaded from {filepath}")
