-- Comparison between raw_centralized_fills and raw_centralized_fills_realtime
-- For BINANCE_SPOT on 2025-08-15

-- ========================================
-- 1. HISTORICAL TABLE (raw_centralized_fills)
-- ========================================
SELECT 
    'HISTORICAL TABLE' as source,
    COUNT(*) as total_records,
    SUM(ABS(signed_qty) * price) as total_volume_usd,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    SUM(CASE WHEN is_taker = true AND fees > 0 THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume_with_fees,
    SUM(fees) as total_fees,
    COUNT(DISTINCT lex_symbol) as unique_symbols
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15';

-- ========================================
-- 2. REALTIME TABLE - ALL RECORDS
-- ========================================
SELECT 
    'REALTIME TABLE (ALL)' as source,
    COUNT(*) as total_records,
    SUM(ABS(signed_qty) * price) as total_volume_usd,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    SUM(CASE WHEN is_taker = true AND fees > 0 THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume_with_fees,
    SUM(fees) as total_fees,
    COUNT(DISTINCT lex_symbol) as unique_symbols
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15';

-- ========================================
-- 3. REALTIME TABLE - WITH LEX_TRADING_SYSTEM FILTER
-- ========================================
SELECT 
    'REALTIME (LEX_TRADING_SYSTEM NOT NULL)' as source,
    COUNT(*) as total_records,
    SUM(ABS(signed_qty) * price) as total_volume_usd,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    SUM(CASE WHEN is_taker = true AND fees > 0 THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume_with_fees,
    SUM(fees) as total_fees,
    COUNT(DISTINCT lex_symbol) as unique_symbols
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
    AND lex_trading_system IS NOT NULL;

-- ========================================
-- 4. CHECK FOR DUPLICATES IN REALTIME TABLE
-- ========================================
SELECT 
    'DUPLICATE CHECK' as analysis,
    COUNT(*) as total_records,
    COUNT(DISTINCT (timestamp, lex_symbol, price, signed_qty)) as unique_trades,
    COUNT(*) - COUNT(DISTINCT (timestamp, lex_symbol, price, signed_qty)) as potential_duplicates
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15';

-- ========================================
-- 5. TOP 5 SYMBOLS - HISTORICAL TABLE
-- ========================================
SELECT 
    'HISTORICAL' as source,
    lex_symbol,
    SUM(ABS(signed_qty) * price) as total_usd_volume,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    COUNT(*) as trade_count
FROM raw_centralized_fills
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
    AND lex_symbol IN ('ETH/USDT', 'ETH/USDC', 'BTC/USDT', 'SOL/USDT', 'HBAR/USDT')
GROUP BY lex_symbol
ORDER BY total_usd_volume DESC;

-- ========================================
-- 6. TOP 5 SYMBOLS - REALTIME TABLE (ALL)
-- ========================================
SELECT 
    'REALTIME_ALL' as source,
    lex_symbol,
    SUM(ABS(signed_qty) * price) as total_usd_volume,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    COUNT(*) as trade_count
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
    AND lex_symbol IN ('ETH/USDT', 'ETH/USDC', 'BTC/USDT', 'SOL/USDT', 'HBAR/USDT')
GROUP BY lex_symbol
ORDER BY total_usd_volume DESC;

-- ========================================
-- 7. TOP 5 SYMBOLS - REALTIME TABLE (FILTERED)
-- ========================================
SELECT 
    'REALTIME_FILTERED' as source,
    lex_symbol,
    SUM(ABS(signed_qty) * price) as total_usd_volume,
    SUM(CASE WHEN is_taker = true THEN ABS(signed_qty) * price ELSE 0 END) as taker_volume,
    COUNT(*) as trade_count
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
    AND lex_trading_system IS NOT NULL
    AND lex_symbol IN ('ETH/USDT', 'ETH/USDC', 'BTC/USDT', 'SOL/USDT', 'HBAR/USDT')
GROUP BY lex_symbol
ORDER BY total_usd_volume DESC;

-- ========================================
-- 8. CHECK LEX_TRADING_SYSTEM VALUES
-- ========================================
SELECT 
    lex_trading_system,
    COUNT(*) as trade_count,
    SUM(ABS(signed_qty) * price) as volume_usd
FROM raw_centralized_fills_realtime
WHERE exchange = 'BINANCE_SPOT'
    AND trade_date = '2025-08-15'
GROUP BY lex_trading_system
ORDER BY volume_usd DESC;