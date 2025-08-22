-- BINANCE_SPOT Volume Analysis for 2025-08-15
-- This query verifies Tony's claims about trading volume discrepancies

-- 1. Overall Summary
SELECT 
    'OVERALL SUMMARY' as analysis_type,
    SUM(ABS(signed_qty) * price) as total_volume_usd,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    SUM(CASE WHEN is_taker = true AND fees > 0 THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume_with_fees,
    SUM(fees) as total_fees,
    COUNT(*) as total_trades
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15';

-- 2. Top 5 Symbols by Volume (matching Tony's list)
SELECT 
    lex_symbol,
    SUM(ABS(signed_qty) * price) as total_usd_volume,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    SUM(fees) as total_fees,
    COUNT(*) as trade_count
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
    AND lex_symbol IN ('ETH/USDT', 'ETH/USDC', 'BTC/USDT', 'SOL/USDT', 'HBAR/USDT')
GROUP BY lex_symbol
ORDER BY total_usd_volume DESC;

-- 3. Hourly Volume Distribution (to check timezone issues)
SELECT 
    DATE_TRUNC('hour', trade_datetime AT TIME ZONE 'UTC') as hour_utc,
    SUM(ABS(signed_qty) * price) as hourly_volume,
    COUNT(*) as trade_count
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_datetime >= '2025-08-15 00:00:00+00'::timestamptz
    AND trade_datetime < '2025-08-16 00:00:00+00'::timestamptz
GROUP BY DATE_TRUNC('hour', trade_datetime AT TIME ZONE 'UTC')
ORDER BY hour_utc;

-- 4. Verify taker vs maker breakdown
SELECT 
    CASE 
        WHEN is_taker = true THEN 'Taker'
        WHEN is_taker = false THEN 'Maker'
        ELSE 'Unknown'
    END as trade_type,
    COUNT(*) as trade_count,
    SUM(ABS(signed_qty) * price) as volume_usd,
    SUM(fees) as total_fees
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
GROUP BY is_taker;

-- 5. Check for any data quality issues
SELECT 
    COUNT(*) as total_records,
    COUNT(DISTINCT lex_symbol) as unique_symbols,
    MIN(trade_datetime) as first_trade,
    MAX(trade_datetime) as last_trade,
    COUNT(CASE WHEN fees < 0 THEN 1 END) as negative_fees,
    COUNT(CASE WHEN price <= 0 THEN 1 END) as invalid_prices,
    COUNT(CASE WHEN is_taker IS NULL THEN 1 END) as null_taker_flag
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15';