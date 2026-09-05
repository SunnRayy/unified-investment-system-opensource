from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Optional
from src.analysis.models import (
    TechnicalSignals, TrendStatus, MACDStatus, RSIStatus, VolumeStatus
)
from src.market_data.fetchers.base import InsufficientDataError

MIN_BARS = 26  # need 26 for MACD EWM(26)

class StockTrendAnalyzer:
    def analyze(self, df: pd.DataFrame, code: str) -> TechnicalSignals:
        df = self._preprocess(df)
        if len(df) < MIN_BARS:
            raise InsufficientDataError(
                f'{code}: need at least {MIN_BARS} bars, got {len(df)}'
            )

        close = df['close'].astype(float)

        # MAs
        ma5_val = close.rolling(5).mean().iloc[-1]
        ma10_val = close.rolling(10).mean().iloc[-1]
        ma20_val = close.rolling(20).mean().iloc[-1]
        ma5 = float(ma5_val) if not np.isnan(ma5_val) else None
        ma10 = float(ma10_val) if not np.isnan(ma10_val) else None
        ma20 = float(ma20_val) if not np.isnan(ma20_val) else None

        # MA alignment score: 2=full bull, 1=partial, 0=bearish
        if ma5 is not None and ma10 is not None and ma20 is not None:
            if ma5 > ma10 and ma10 > ma20:
                ma_alignment_score = 2
            elif ma5 > ma10 or ma10 > ma20:
                ma_alignment_score = 1
            else:
                ma_alignment_score = 0
        else:
            ma_alignment_score = 0

        # MACD: EWM(span=12) - EWM(span=26), signal=EWM(span=9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_series = ema12 - ema26
        signal_series = macd_series.ewm(span=9, adjust=False).mean()
        hist_series = macd_series - signal_series

        macd_line = float(macd_series.iloc[-1])
        macd_signal_val = float(signal_series.iloc[-1])
        macd_hist_val = float(hist_series.iloc[-1])

        # MACD status: event-first (cross detection), then directional
        if len(hist_series) >= 2:
            prev_hist = float(hist_series.iloc[-2])
            curr_hist = macd_hist_val
            if prev_hist < 0 and curr_hist >= 0:
                macd_status = MACDStatus.GOLDEN_CROSS
            elif prev_hist > 0 and curr_hist <= 0:
                macd_status = MACDStatus.DEATH_CROSS
            elif curr_hist > 0:
                macd_status = MACDStatus.BULLISH
            elif curr_hist < 0:
                macd_status = MACDStatus.BEARISH
            else:
                macd_status = MACDStatus.NEUTRAL
        else:
            macd_status = MACDStatus.NEUTRAL

        # RSI(14): Wilder smoothing — ewm(alpha=1/14, adjust=False, min_periods=14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0/14.0, adjust=False, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1.0/14.0, adjust=False, min_periods=14).mean()
        last_avg_gain = float(avg_gain.iloc[-1])
        last_avg_loss = float(avg_loss.iloc[-1])

        if np.isnan(last_avg_gain) or np.isnan(last_avg_loss):
            rsi_value = None
        elif last_avg_loss == 0 and last_avg_gain > 0:
            rsi_value = 100.0
        elif last_avg_loss == 0 and last_avg_gain == 0:
            rsi_value = 50.0
        else:
            rs_val = last_avg_gain / last_avg_loss
            rsi_value = 100.0 - (100.0 / (1.0 + rs_val))

        if rsi_value is None:
            rsi_status = RSIStatus.NEUTRAL
        elif rsi_value >= 70:
            rsi_status = RSIStatus.OVERBOUGHT
        elif rsi_value <= 30:
            rsi_status = RSIStatus.OVERSOLD
        else:
            rsi_status = RSIStatus.NEUTRAL

        # Volume ratio
        volume_ratio: Optional[float] = None
        if 'volume' in df.columns:
            vol = pd.to_numeric(df['volume'], errors='coerce')
            if vol.notna().sum() >= 5:
                avg_vol = vol.rolling(20, min_periods=5).mean().iloc[-1]
                latest_vol = vol.iloc[-1]
                if not np.isnan(avg_vol) and avg_vol > 0 and not np.isnan(latest_vol):
                    volume_ratio = float(latest_vol / avg_vol)

        if volume_ratio is None:
            volume_status = VolumeStatus.NORMAL
        elif volume_ratio >= 2.0:
            volume_status = VolumeStatus.SURGING
        elif volume_ratio >= 1.5:
            volume_status = VolumeStatus.HIGH
        elif volume_ratio >= 0.7:
            volume_status = VolumeStatus.NORMAL
        else:
            volume_status = VolumeStatus.LOW

        # Support/resistance: rolling 20-day lows/highs, cluster within 1.5%
        roll_low = close.rolling(20, min_periods=10).min()
        roll_high = close.rolling(20, min_periods=10).max()
        support_levels = self._cluster_levels(roll_low.dropna().tolist(), ascending=True)
        resistance_levels = self._cluster_levels(roll_high.dropna().tolist(), ascending=False)

        # Signal scoring
        ma_pts = {2: 40, 1: 20, 0: 0}[ma_alignment_score]
        macd_pts_map = {
            MACDStatus.GOLDEN_CROSS: 30,
            MACDStatus.BULLISH: 20,
            MACDStatus.NEUTRAL: 15,
            MACDStatus.BEARISH: 10,
            MACDStatus.DEATH_CROSS: 0,
        }
        macd_pts = macd_pts_map[macd_status]
        rsi_pts_map = {RSIStatus.NEUTRAL: 20, RSIStatus.OVERBOUGHT: 10, RSIStatus.OVERSOLD: 10}
        rsi_pts = rsi_pts_map[rsi_status]
        vol_pts_map = {VolumeStatus.SURGING: 10, VolumeStatus.HIGH: 8, VolumeStatus.NORMAL: 5, VolumeStatus.LOW: 2}
        vol_pts = vol_pts_map[volume_status] if volume_ratio is not None else 5

        signal_score = ma_pts + macd_pts + rsi_pts + vol_pts
        trend_direction_score = ma_pts + macd_pts  # 0-70

        # TrendStatus from trend_direction_score
        if trend_direction_score >= 56:
            trend_status = TrendStatus.STRONG_BULL
        elif trend_direction_score >= 42:
            trend_status = TrendStatus.BULL
        elif trend_direction_score >= 28:
            trend_status = TrendStatus.NEUTRAL
        elif trend_direction_score >= 14:
            trend_status = TrendStatus.BEAR
        else:
            trend_status = TrendStatus.STRONG_BEAR

        return TechnicalSignals(
            trend_status=trend_status,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma_alignment_score=ma_alignment_score,
            rsi_value=rsi_value,
            rsi_status=rsi_status,
            macd_line=macd_line,
            macd_signal=macd_signal_val,
            macd_hist=macd_hist_val,
            macd_status=macd_status,
            volume_ratio=volume_ratio,
            volume_status=volume_status,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            signal_score=signal_score,
            trend_direction_score=trend_direction_score,
        )

    def _preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.dropna(subset=['date'])
            df = df.sort_values('date').drop_duplicates(subset='date', keep='last')
        for col in ['close', 'open', 'high', 'low']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close'])
        df = df.reset_index(drop=True)
        return df

    def _cluster_levels(self, values: list, ascending: bool, top_n: int = 3) -> list:
        if not values:
            return []
        sorted_vals = sorted(set(values)) if ascending else sorted(set(values), reverse=True)
        result = []
        for v in sorted_vals:
            if not result or abs(v - result[-1]) / max(abs(result[-1]), 1e-9) > 0.015:
                result.append(v)
            if len(result) >= top_n:
                break
        return result
