"""Unit tests for shared strategy logic across all 3 exchange strategies."""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import os


def make_price_df(close_prices):
    """Build a DataFrame with a DatetimeIndex from a list of close prices."""
    dates = [datetime.now() - timedelta(days=len(close_prices) - i) for i in range(len(close_prices))]
    df = pd.DataFrame({'close': close_prices}, index=pd.DatetimeIndex(dates))
    df.index.name = 'timestamp'
    return df


def compute_ma(df, period):
    df = df.copy()
    df['MA'] = df['close'].rolling(window=period).mean()
    return df['MA'].iloc[-1]


class TestMACalculation(unittest.TestCase):

    def test_ma_equals_mean(self):
        prices = [100.0] * 10
        df = make_price_df(prices)
        ma = compute_ma(df, 10)
        self.assertAlmostEqual(ma, 100.0)

    def test_ma_with_varying_prices(self):
        prices = list(range(1, 11))  # 1..10
        df = make_price_df(prices)
        ma = compute_ma(df, 10)
        self.assertAlmostEqual(ma, 5.5)

    def test_ma_uses_last_n_points(self):
        # First 5 points are noise, last 5 are all 200
        prices = [1.0] * 5 + [200.0] * 5
        df = make_price_df(prices)
        ma = compute_ma(df, 5)
        self.assertAlmostEqual(ma, 200.0)

    def test_ma_insufficient_data_returns_nan(self):
        prices = [100.0] * 5
        df = make_price_df(prices)
        ma = compute_ma(df, 10)
        self.assertTrue(pd.isna(ma))

    def test_effective_ma_period_capped_at_data_length(self):
        prices = [100.0] * 5
        df = make_price_df(prices)
        effective_period = min(730, len(df))
        self.assertEqual(effective_period, 5)


class TestBuyConditions(unittest.TestCase):

    def _check_conditions(self, fng_value, current_price, latest_ma, fng_threshold=25, ma_threshold=0.1):
        buy_fng = fng_value is not None and fng_value <= fng_threshold
        buy_ma = current_price <= (1 - ma_threshold) * latest_ma
        overlap = buy_fng and buy_ma
        return buy_fng, buy_ma, overlap

    def test_no_buy_when_conditions_not_met(self):
        buy_fng, buy_ma, overlap = self._check_conditions(fng_value=50, current_price=90000, latest_ma=90000)
        self.assertFalse(buy_fng)
        self.assertFalse(buy_ma)
        self.assertFalse(overlap)

    def test_fng_buy_only(self):
        buy_fng, buy_ma, overlap = self._check_conditions(fng_value=20, current_price=90000, latest_ma=90000)
        self.assertTrue(buy_fng)
        self.assertFalse(buy_ma)
        self.assertFalse(overlap)

    def test_ma_buy_only(self):
        # price is 10% below MA
        buy_fng, buy_ma, overlap = self._check_conditions(fng_value=50, current_price=81000, latest_ma=90000)
        self.assertFalse(buy_fng)
        self.assertTrue(buy_ma)
        self.assertFalse(overlap)

    def test_overlap_both_conditions(self):
        buy_fng, buy_ma, overlap = self._check_conditions(fng_value=20, current_price=81000, latest_ma=90000)
        self.assertTrue(buy_fng)
        self.assertTrue(buy_ma)
        self.assertTrue(overlap)

    def test_fng_boundary_at_threshold(self):
        buy_fng, _, _ = self._check_conditions(fng_value=25, current_price=90000, latest_ma=90000)
        self.assertTrue(buy_fng)

    def test_fng_boundary_above_threshold(self):
        buy_fng, _, _ = self._check_conditions(fng_value=26, current_price=90000, latest_ma=90000)
        self.assertFalse(buy_fng)

    def test_ma_boundary_exactly_at_threshold(self):
        # price == (1 - 0.1) * ma → buy_ma should be True (<=)
        _, buy_ma, _ = self._check_conditions(fng_value=50, current_price=81000.0, latest_ma=90000.0)
        self.assertTrue(buy_ma)

    def test_ma_boundary_just_above_threshold(self):
        _, buy_ma, _ = self._check_conditions(fng_value=50, current_price=81001.0, latest_ma=90000.0)
        self.assertFalse(buy_ma)

    def test_fng_none_does_not_trigger(self):
        buy_fng, _, overlap = self._check_conditions(fng_value=None, current_price=81000, latest_ma=90000)
        self.assertFalse(buy_fng)
        self.assertFalse(overlap)


class TestMAThresholdAutoCorrection(unittest.TestCase):

    def _autocorrect(self, raw_value):
        ma_threshold = float(raw_value)
        if ma_threshold >= 1:
            ma_threshold = ma_threshold / 100
        return ma_threshold

    def test_decimal_unchanged(self):
        self.assertAlmostEqual(self._autocorrect(0.1), 0.1)

    def test_whole_number_corrected(self):
        self.assertAlmostEqual(self._autocorrect(10), 0.1)

    def test_boundary_value_one_corrected(self):
        self.assertAlmostEqual(self._autocorrect(1), 0.01)

    def test_zero_unchanged(self):
        self.assertAlmostEqual(self._autocorrect(0.0), 0.0)

    def test_large_percentage_corrected(self):
        self.assertAlmostEqual(self._autocorrect(50), 0.5)


class TestBuyAmountValidation(unittest.TestCase):

    def _validate_amount(self, raw, min_val=0.000001, max_val=10.0):
        """Returns (is_valid, error_message)."""
        if raw is None:
            return True, None
        try:
            val = float(raw)
            if val != 0.0 and not (min_val <= val <= max_val):
                return False, f"value {val} outside [{min_val}, {max_val}]"
            return True, None
        except ValueError:
            return False, f"not a valid number: {raw}"

    def test_valid_small_amount(self):
        self.assertTrue(self._validate_amount('0.0001')[0])

    def test_valid_zero_disables(self):
        self.assertTrue(self._validate_amount('0')[0])

    def test_invalid_too_large(self):
        self.assertFalse(self._validate_amount('11.0')[0])

    def test_invalid_negative(self):
        self.assertFalse(self._validate_amount('-0.001')[0])

    def test_invalid_text(self):
        self.assertFalse(self._validate_amount('abc')[0])

    def test_none_is_valid(self):
        self.assertTrue(self._validate_amount(None)[0])

    def test_boundary_max(self):
        self.assertTrue(self._validate_amount('10.0')[0])

    def test_boundary_min(self):
        self.assertTrue(self._validate_amount('0.000001')[0])


class TestTradeLogCSV(unittest.TestCase):

    def test_log_trade_creates_file_with_header(self):
        import csv, tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp:
            trade_file = tmp.name

        try:
            os.remove(trade_file)  # ensure it doesn't exist

            def log_trade(exchange, btc_amount, price, reason, order_id, trade_file=trade_file):
                row = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'exchange': exchange,
                    'btc_amount': btc_amount,
                    'price_usdt': price,
                    'usdt_total': round(btc_amount * price, 2),
                    'trigger_reason': reason,
                    'order_id': order_id,
                }
                file_exists = os.path.exists(trade_file)
                with open(trade_file, 'a', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=row.keys())
                    if not file_exists:
                        writer.writeheader()
                    writer.writerow(row)

            log_trade('coinex', 0.0001, 85000.0, 'fng_only', 'order123')

            with open(trade_file) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['exchange'], 'coinex')
            self.assertAlmostEqual(float(rows[0]['btc_amount']), 0.0001)
            self.assertEqual(rows[0]['trigger_reason'], 'fng_only')
        finally:
            if os.path.exists(trade_file):
                os.remove(trade_file)


if __name__ == '__main__':
    unittest.main()
