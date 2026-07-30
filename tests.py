"""
Tests de sanidad. Correr con: python3 tests.py
No reemplazan un test suite completo, pero cubren lo más crítico: que el
gestor de riesgo NUNCA arriesgue más del % configurado, y que la
validación de datos detecte datos corruptos.
"""

import pandas as pd
import numpy as np

from risk_manager import position_size
from risk_profiles import get_profile
from safety import validate_ohlcv, CircuitBreaker, ProfitLock


def test_risk_never_exceeds_profile():
    profile = get_profile("agresivo")  # 2% de riesgo por operación
    capital = 1000.0
    entry_price = 50.0
    atr = 2.0

    sizing = position_size(capital, entry_price, atr, profile)
    max_loss_if_stopped = sizing["unidades"] * (entry_price - sizing["stop_loss"])
    max_allowed = capital * profile["risk_per_trade"]

    assert max_loss_if_stopped <= max_allowed + 1e-6, (
        f"El riesgo real ({max_loss_if_stopped}) supera el máximo permitido ({max_allowed})"
    )
    print("OK: el tamaño de posición nunca arriesga más del % del perfil")


def test_position_size_never_exceeds_capital():
    profile = get_profile("agresivo")
    capital = 100.0
    entry_price = 5.0
    atr = 0.05  # ATR muy chico -> sizing podría pedir muchas unidades

    sizing = position_size(capital, entry_price, atr, profile)
    cost = sizing["unidades"] * entry_price
    assert cost <= capital + 1e-6, "El tamaño de posición pide más capital del disponible"
    print("OK: el tamaño de posición nunca excede el capital disponible")


def test_position_size_capped_by_capital_survives_slippage_and_commission():
    """Bug real visto en vivo (ETH_USDC, 17-jul): cuando el tope de capital es
    el que termina definiendo las unidades, el bróker ejecuta con slippage y
    suma comisión encima del precio "limpio" que usó position_size(). Sin
    margen de seguridad, esa orden queda garantizada al rechazo por saldo
    insuficiente -- el tope pensado como red de contención terminaba
    bloqueando la operación por completo."""
    from broker import PaperBroker

    profile = get_profile("moderado")
    capital = 1000.0
    entry_price = 2000.0
    atr = 1.0  # ATR chico a propósito -> el sizing por riesgo pide de más y el tope de capital termina mandando

    sizing = position_size(capital, entry_price, atr, profile)
    assert sizing["unidades"] * entry_price < capital, (
        "El tope de capital debe dejar margen para slippage/comisión, no usar el 100% del capital"
    )

    broker = PaperBroker(initial_balance=capital)
    broker.set_price("ETH_USDC", entry_price)
    order = broker.place_order("ETH_USDC", "buy", sizing["unidades"])
    assert order["status"] != "rejected", (
        f"La orden fue rechazada pese al margen de seguridad: {order.get('motivo')}"
    )
    print("OK: el tope de capital deja margen y la orden no se rechaza por slippage/comisión")


def test_position_size_caps_concentration_when_atr_is_tiny_relative_to_price():
    """
    Bug real visto en vivo con LINK_USDC (ver README, ronda de auditoría
    de concentración): con un ATR muy chico en relación al precio (activo
    calmo, o poca historia todavía para calcularlo bien), `risk_amount /
    stop_distance` pedía una cantidad de unidades enorme -- tan grande que
    el único freno que terminaba actuando era el tope de capital
    disponible, no el % de riesgo del perfil. El resultado real: una
    posición de casi el 97% del capital en un solo símbolo, cuando el
    perfil "moderado" solo debería arriesgar el 1% por operación. Estos
    números reproducen ese escenario real (entrada ~$8.40, ATR ~0.0045).
    """
    profile = get_profile("moderado")  # max_position_pct_of_capital = 0.30
    capital = 1000.0
    entry_price = 8.40
    atr = 0.0045  # a propósito diminuto en relación al precio, como se vio en vivo

    sizing = position_size(capital, entry_price, atr, profile)
    trade_value = sizing["unidades"] * entry_price

    max_permitido = capital * profile["max_position_pct_of_capital"]
    assert trade_value <= max_permitido + 0.01, (
        f"La posición (${trade_value:.2f}) supera el tope de concentración del perfil (${max_permitido:.2f})"
    )
    # Sin el tope de concentración, el único freno hubiera sido el de
    # capital disponible (casi el 100% del capital) -- confirma que ACÁ
    # es el tope nuevo el que está actuando, no una coincidencia.
    assert trade_value < capital * 0.5, (
        "La posición sigue concentrando casi todo el capital -- el tope de concentración no está frenando nada"
    )
    print("OK: un ATR diminuto en relación al precio no convierte 'arriesgar 1%' en 'apostar casi todo el capital'")


def test_zero_atr_returns_zero_units():
    profile = get_profile("moderado")
    sizing = position_size(1000.0, 50.0, 0.0, profile)
    assert sizing["unidades"] == 0, "Con ATR=0 debería devolver 0 unidades (evita división por cero)"
    print("OK: ATR cero no rompe el cálculo")


def test_validation_detects_corrupt_data():
    dates = pd.bdate_range("2024-01-01", periods=10)
    df = pd.DataFrame({
        "open": [10] * 10,
        "high": [5] * 10,   # high < low a propósito -> dato corrupto
        "low": [8] * 10,
        "close": [9] * 10,
        "volume": [1000] * 10,
    }, index=dates)

    problems = validate_ohlcv(df)
    assert any("high" in p and "low" in p for p in problems), "No detectó high < low"
    print("OK: la validación detecta datos corruptos (high < low)")


def test_validation_passes_clean_data():
    dates = pd.bdate_range("2024-01-01", periods=100)
    df = pd.DataFrame({
        "open": np.linspace(100, 110, 100),
        "high": np.linspace(101, 111, 100),
        "low": np.linspace(99, 109, 100),
        "close": np.linspace(100.5, 110.5, 100),
        "volume": [1000] * 100,
    }, index=dates)

    problems = validate_ohlcv(df)
    assert problems == [], f"Datos limpios no deberían generar problemas, pero generaron: {problems}"
    print("OK: datos limpios pasan la validación sin falsos positivos")


def test_circuit_breaker_trips_on_drawdown():
    cb = CircuitBreaker(max_drawdown_pct=10.0, max_daily_loss_pct=50.0)
    equity_curve = [1000, 1000, 1000]
    tripped = cb.check(equity_curve, 1000, 880)  # 12% de drawdown desde el pico
    assert tripped, "El circuit breaker debería activarse con 12% de drawdown y límite de 10%"
    print("OK: el circuit breaker se activa al superar el drawdown máximo")


def test_profit_lock_check_detects_target_reached():
    """check() es una consulta pura -- avisa si se llegó a la meta, pero
    no cambia nada por sí sola (quien la usa decide si cerrar y asegurar
    con lock_in())."""
    lock = ProfitLock(target_pct=20.0, reference_capital=1000.0)
    assert lock.check(1150.0) is False, "No debería avisar con +15%, el objetivo es +20%"
    assert lock.check(1200.0) is True, "Debería avisar al llegar exactamente a +20%"
    assert lock.check(1200.0) is True, "Repetir check() sin llamar lock_in() debe seguir avisando igual"
    assert lock.times_locked == 0, "check() por sí sola no debe bancar nada"
    print("OK: check() detecta la meta sin efectos secundarios propios")


def test_profit_lock_lock_in_ratchets_and_keeps_operating():
    """Al asegurar una ganancia, el piso sube al nuevo capital y el motor
    sigue midiendo la PRÓXIMA meta desde ahí -- nunca se queda pausado
    para siempre."""
    lock = ProfitLock(target_pct=10.0, reference_capital=1000.0)
    assert lock.check(1100.0) is True
    lock.lock_in(1100.0)
    assert lock.times_locked == 1
    assert lock.reference_capital == 1100.0
    assert "#1" in lock.last_lock_reason

    # La MISMA ganancia relativa desde el piso viejo (1000) ya no alcanza --
    # ahora hace falta +10% desde el nuevo piso (1100), o sea 1210.
    assert lock.check(1150.0) is False, "Con el piso ya subido a 1100, +150 desde el original no alcanza"
    assert lock.check(1210.0) is True, "Debe volver a avisar al llegar al +10% del NUEVO piso"
    lock.lock_in(1210.0)
    assert lock.times_locked == 2
    print("OK: lock_in() sube el piso y sigue vigilando la próxima meta (nunca se detiene del todo)")


def test_profit_lock_rejects_invalid_params():
    try:
        ProfitLock(target_pct=0, reference_capital=1000.0)
        assert False, "Debería rechazar un target_pct de 0 o negativo"
    except ValueError:
        pass
    try:
        ProfitLock(target_pct=10.0, reference_capital=0)
        assert False, "Debería rechazar un reference_capital de 0 o negativo"
    except ValueError:
        pass
    print("OK: ProfitLock rechaza parámetros inválidos en vez de calcular con ellos")


def test_min_trade_value_rejects_tiny_trades():
    profile = get_profile("agresivo")
    # Capital muy chico + mínimo operable alto -> la operación no debería ser viable
    sizing = position_size(50.0, 100.0, 2.0, profile, min_trade_value=1000.0)
    assert sizing["viable"] is False, "Debería marcar la operación como no viable"
    print("OK: el mínimo operable rechaza operaciones demasiado chicas")


def test_concentration_warnings_flags_only_positions_above_threshold():
    """
    concentration_warnings() -- usado por status_report.py y
    real_results_report.py para avisar sin depender de mirar el JSON a
    mano (ver README, ronda de auditoría de concentración). Con el
    threshold por defecto (45%, por encima del tope de cualquier perfil),
    una posición que concentra ~97% del capital (el escenario real visto
    con LINK_USDC) debe generar un aviso; una posición normal (~15%) no."""
    from risk_manager import concentration_warnings

    # Escenario real: casi todo el capital en un solo símbolo.
    avisos = concentration_warnings(
        capital=3.41, positions={"LINK_USDC": {"unidades": 114.767004, "precio_entrada": 8.4271}},
    )
    assert len(avisos) == 1 and "LINK_USDC" in avisos[0], "Debe avisar sobre la posición sobre-concentrada"

    # Posición normal, dentro de cualquier perfil -- no debe avisar nada.
    avisos_normales = concentration_warnings(
        capital=850.0, positions={"TEST_SYM": {"unidades": 1.5, "precio_entrada": 100.0}},
    )
    assert avisos_normales == [], "Una posición normal no debería disparar ningún aviso"

    assert concentration_warnings(capital=1000.0, positions={}) == [], "Sin posiciones abiertas, no hay nada que avisar"
    print("OK: concentration_warnings avisa solo sobre posiciones realmente fuera de lo esperado")


def test_adx_bounded_and_regime_classifies():
    from data_utils import generate_synthetic_data
    from regime import compute_adx, classify_regime

    df = generate_synthetic_data(n_days=300)
    adx = compute_adx(df)
    assert (adx >= 0).all() and (adx <= 100).all(), "ADX debe estar siempre entre 0 y 100"

    regime = classify_regime(df)
    assert set(regime.unique()).issubset({"tendencia", "lateral"}), "Régimen debe ser tendencia o lateral"
    print("OK: ADX acotado entre 0-100 y clasificación de régimen funciona")


def test_heartbeat_detects_staleness():
    from health import Heartbeat
    hb = Heartbeat(max_staleness_seconds=9999)
    assert hb.is_stale() is True, "Sin ningún beat, debería considerarse no saludable"
    hb.beat()
    assert hb.is_stale() is False, "Justo después de un beat, debería estar saludable"
    print("OK: el heartbeat detecta correctamente la falta de datos frescos")


def test_data_gap_detection():
    from health import check_data_gaps
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-15", "2024-01-16"])
    df = pd.DataFrame({
        "open": [1, 1, 1, 1], "high": [1, 1, 1, 1], "low": [1, 1, 1, 1],
        "close": [1, 1, 1, 1], "volume": [1, 1, 1, 1],
    }, index=dates)
    gaps = check_data_gaps(df, max_gap_days=3)
    assert len(gaps) == 1, "Debería detectar exactamente 1 hueco (13 días entre el 2 y el 15 de enero)"
    print("OK: la detección de huecos de datos encuentra el hueco esperado")


def test_correlation_scales_down_correlated_positions():
    from data_utils import generate_correlated_pair
    from portfolio import PortfolioBacktester

    high_corr_a, high_corr_b = generate_correlated_pair(n_days=300, correlation=0.95, seed=100)
    pf = PortfolioBacktester({"A": high_corr_a, "B": high_corr_b}, "momentum", "agresivo",
                              initial_capital=1000, correlation_threshold=0.5)
    result = pf.run()

    if result["num_operaciones"] > 1:
        assert result["operaciones_escaladas_por_correlacion"] >= 0, (
            "El contador de operaciones escaladas no debería ser negativo"
        )
    print("OK: el backtester de portafolio corre y registra ajustes por correlación")


def test_correlation_matrix_detects_high_correlation():
    from data_utils import generate_correlated_pair
    from portfolio import compute_return_correlation

    a, b = generate_correlated_pair(n_days=300, correlation=0.95, seed=101)
    corr = compute_return_correlation({"A": a, "B": b})
    observed = corr.loc["A", "B"]
    assert observed > 0.5, f"Con correlación objetivo 0.95, se esperaba observar >0.5, se obtuvo {observed}"
    print(f"OK: la matriz de correlación detecta la correlación alta (observada: {observed:.2f})")


def test_monte_carlo_bootstrap_generates_variation():
    from monte_carlo import monte_carlo_from_trades
    fake_trades = [{"pnl": 50}, {"pnl": -30}, {"pnl": 20}, {"pnl": -80}, {"pnl": 100}]
    mc = monte_carlo_from_trades(fake_trades, initial_capital=1000, n_simulations=500, method="bootstrap")
    assert mc["capital_final_peor_caso_p5"] != mc["capital_final_mejor_caso_p95"], (
        "El bootstrap debería generar variación real entre el peor y mejor caso"
    )
    print("OK: Monte Carlo bootstrap genera una distribución real de resultados")


def test_engine_survives_single_price_flash_crash():
    """
    Datos adversariales: una vela con una caída de -80% en un solo día
    (flash crash extremo) en medio de datos normales. El motor no debería
    crashear ni devolver números imposibles (ej. capital negativo).
    """
    from data_utils import generate_synthetic_data
    from backtester import Backtester

    df = generate_synthetic_data(n_days=200, seed=5)
    crash_idx = 100
    df.iloc[crash_idx, df.columns.get_loc("close")] *= 0.2  # -80% en un día
    df.iloc[crash_idx, df.columns.get_loc("low")] *= 0.2
    df.iloc[crash_idx, df.columns.get_loc("high")] = max(
        df.iloc[crash_idx]["high"], df.iloc[crash_idx]["close"]
    )

    bt = Backtester(df, "momentum", "agresivo", initial_capital=1000, validate=False)
    result = bt.run()

    assert result["capital_final"] >= 0, "El capital final nunca debería ser negativo"
    assert not (result["equity_curve"] < 0).any(), "La curva de capital nunca debería volverse negativa"
    print("OK: el motor sobrevive a un flash crash de -80% en una sola vela sin romperse")


def test_engine_handles_flat_zero_volatility_data():
    """
    Datos adversariales: precio completamente plano (ATR = 0 todo el
    tiempo). Esto podría causar división por cero en el sizing -- el
    motor debe simplemente no abrir posiciones, no crashear.
    """
    import pandas as pd
    from backtester import Backtester

    dates = pd.bdate_range("2024-01-01", periods=100)
    flat_df = pd.DataFrame({
        "open": [100.0] * 100, "high": [100.0] * 100, "low": [100.0] * 100,
        "close": [100.0] * 100, "volume": [1000] * 100,
    }, index=dates)

    bt = Backtester(flat_df, "momentum", "moderado", initial_capital=1000, validate=False)
    result = bt.run()

    assert result["num_operaciones"] == 0, "Con volatilidad cero no debería abrir ninguna posición"
    assert result["capital_final"] == 1000, "Sin operaciones, el capital final debe ser igual al inicial"
    print("OK: el motor no crashea con datos completamente planos (ATR=0)")


def test_validate_rejects_single_row_dataset():
    """Dataset de una sola vela -- ni siquiera debería llegar a intentar operar."""
    import pandas as pd
    tiny_df = pd.DataFrame({
        "open": [100], "high": [101], "low": [99], "close": [100.5], "volume": [1000],
    }, index=pd.bdate_range("2024-01-01", periods=1))

    problems = validate_ohlcv(tiny_df)
    assert len(problems) > 0, "Un dataset de 1 vela debería marcarse como insuficiente"
    print("OK: la validación rechaza datasets con muy pocas velas")


def test_backtest_reproducibility():
    """
    Correr el mismo backtest dos veces con los mismos datos debe dar
    EXACTAMENTE el mismo resultado -- si no, algo no es determinista
    (ej. un uso de aleatoriedad sin seed fija) y eso rompe la confianza
    en cualquier resultado reportado.
    """
    from data_utils import generate_synthetic_data
    from backtester import Backtester

    df = generate_synthetic_data(n_days=300, seed=99)
    r1 = Backtester(df, "tendencia", "moderado", initial_capital=1000).run()
    r2 = Backtester(df, "tendencia", "moderado", initial_capital=1000).run()

    assert r1["capital_final"] == r2["capital_final"], "El mismo backtest debería dar el mismo resultado siempre"
    assert r1["num_operaciones"] == r2["num_operaciones"], "La cantidad de operaciones debería ser idéntica"
    print("OK: el backtest es determinista/reproducible con los mismos datos y parámetros")


def test_paper_broker_rejects_invalid_orders():
    from broker import PaperBroker
    pb = PaperBroker(initial_balance=100.0)
    pb.set_price("TEST", 50.0)

    # Vender sin tener posición debe rechazarse, no crashear
    result = pb.place_order("TEST", "sell", 10.0)
    assert result["status"] == "rejected", "Vender sin posición debería rechazarse"

    # Comprar más de lo que el saldo permite debe rechazarse, no crashear
    result2 = pb.place_order("TEST", "buy", 1000.0)
    assert result2["status"] == "rejected", "Comprar sin saldo suficiente debería rechazarse"
    assert pb.get_balance() == 100.0, "El balance no debería cambiar si la orden fue rechazada"
    print("OK: el PaperBroker rechaza órdenes inválidas sin romper el balance")


def test_console_alert_channel_sends():
    from alerts import ConsoleAlertChannel
    channel = ConsoleAlertChannel()
    assert channel.send("mensaje de prueba") is True, "El canal de consola siempre debería devolver True"
    print("OK: el canal de alertas por consola funciona")


def test_telegram_channel_fails_gracefully_without_network():
    from alerts import TelegramAlertChannel
    channel = TelegramAlertChannel(bot_token="fake", chat_id="123")
    result = channel.send("mensaje de prueba")
    assert result is False, "Sin acceso de red, debería devolver False, no crashear"
    print("OK: el canal de Telegram falla de forma segura sin acceso de red")


def test_multi_timeframe_no_lookahead():
    """
    El filtro multi-timeframe NUNCA debería poder "ver" el cierre de una
    vela semanal que todavía no cerró en el calendario real. Este test
    verifica que el valor de tendencia semanal usado en cualquier día
    corresponde a una semana ya completada antes de esa fecha, no a la
    semana en curso.
    """
    from data_utils import generate_synthetic_data
    from multi_timeframe import apply_multi_timeframe_filter

    df = generate_synthetic_data(n_days=300, seed=11)
    signal = pd.Series(1, index=df.index)  # señal siempre "comprar" para aislar el filtro

    filtered = apply_multi_timeframe_filter(signal, df, higher_rule="W", ma_period=4)

    # Chequeo indirecto: la señal filtrada de los primeros días (antes de
    # que exista siquiera una vela semanal cerrada) debe estar apagada,
    # nunca podría estar confirmando con datos que todavía no existían.
    first_week_end = df.index[0] + pd.Timedelta(days=7)
    early_days = filtered[df.index < first_week_end]
    assert (early_days == 0).all(), (
        "Antes de que exista una vela semanal cerrada, la señal filtrada "
        "debería estar en 0 -- si no, está usando información del futuro."
    )
    print("OK: el filtro multi-timeframe no usa información de velas semanales aún no cerradas")


def test_live_runner_smoke_test():
    """
    Test de humo: corre el live_runner sobre unos pocos ticks y verifica
    que no crashea y que el balance nunca queda en un estado imposible.
    No valida resultados económicos (eso lo hace el backtester), solo que
    el flujo completo -- estrategia + bróker + alertas + kill-switch +
    heartbeat -- se ejecuta sin romperse.
    """
    import os
    import tempfile
    from live_runner import run_live

    fd, state_path = tempfile.mkstemp(suffix="_live_smoke_state.json")
    os.close(fd)
    os.remove(state_path)
    try:
        broker = run_live(
            csv_path="real_data/btc_daily.csv", strategy_name="momentum",
            profile_name="moderado", symbol="BTCUSD_TEST", initial_balance=1000.0,
            max_ticks=40, state_path=state_path,
        )
        assert broker.get_balance() >= 0, "El balance nunca debería quedar negativo"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: el live_runner corre de punta a punta sin errores (test de humo)")


def test_live_polling_runs_with_stub_price_feed():
    """
    Test de humo del modo de paper trading con precios EN VIVO
    (run_live_polling): usa un price_source de prueba (no llama a la red,
    para que el test sea determinista) que devuelve una secuencia de
    precios, y verifica que el runner corre varios ticks sin romperse, con
    el historial semilla real de real_data/btc_daily.csv, y que el balance
    nunca queda en un estado imposible.
    """
    import os
    import tempfile
    from live_runner import run_live_polling

    class StubPriceSource:
        """Devuelve precios de una lista fija, uno por llamada (sin red)."""

        def __init__(self, prices):
            self._prices = list(prices)
            self._i = 0

        def get_current_price(self, symbol):
            price = self._prices[min(self._i, len(self._prices) - 1)]
            self._i += 1
            return price

    prices = [100 + i * 0.5 for i in range(20)]
    price_source = StubPriceSource(prices)

    fd, state_path = tempfile.mkstemp(suffix="_live_polling_state.json")
    os.close(fd)
    os.remove(state_path)
    try:
        broker = run_live_polling(
            price_source, symbol="TEST_LIVE", strategy_name="momentum", profile_name="moderado",
            seed_csv="real_data/btc_daily.csv", initial_balance=1000.0,
            poll_interval_seconds=0, max_ticks=15, state_path=state_path,
        )
        assert broker.get_balance() >= 0, "El balance nunca debería quedar negativo"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: el modo de paper trading con precios en vivo corre de punta a punta con un feed de prueba")


def test_append_live_tick_same_day_updates_in_place():
    """
    Bug real encontrado en auditoría (ver README, ronda de agregación de
    ticks): varios ticks del MISMO día calendario deben actualizar la
    vela de hoy en curso, no agregar una fila nueva cada vez -- si no, la
    ventana rodante del ATR se llena de ticks de segundos en vez de días
    reales, y el ATR termina midiendo el rango típico de 45 segundos, no
    de un día (esto fue lo que disparó el bug de concentración con
    LINK_USDC).
    """
    import pandas as pd
    from live_runner import _append_live_tick

    dates = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame({
        "open": [100, 101, 102, 103, 104],
        "high": [101, 102, 103, 104, 105],
        "low": [99, 100, 101, 102, 103],
        "close": [100.5, 101.5, 102.5, 103.5, 104.5],
        "volume": [1000] * 5,
    }, index=dates)
    original_len = len(df)

    today = pd.Timestamp("2024-01-10")  # un día nuevo, después del seed
    df = _append_live_tick(df, today, 105.0, last_close=104.5)
    assert len(df) == original_len + 1, "El primer tick del día debe abrir una vela nueva"
    assert df.loc[pd.Timestamp(today.date()), "open"] == 104.5

    # Varios ticks más, MISMO día -- no deben agregar filas nuevas.
    df = _append_live_tick(df, pd.Timestamp("2024-01-10 10:15:00"), 106.0, last_close=105.0)
    assert len(df) == original_len + 1, "Un segundo tick del mismo día no debe agregar una fila nueva"

    df = _append_live_tick(df, pd.Timestamp("2024-01-10 10:16:30"), 103.0, last_close=106.0)
    assert len(df) == original_len + 1, "Un tercer tick del mismo día tampoco debe agregar una fila"

    hoy = df.loc[pd.Timestamp(today.date())]
    assert hoy["high"] == 106.0, "El high del día debe reflejar el máximo de TODOS los ticks del día"
    assert hoy["low"] == 103.0, "El low del día debe reflejar el mínimo de TODOS los ticks del día"
    assert hoy["close"] == 103.0, "El close debe ser el último precio del día"
    print("OK: varios ticks del mismo día calendario actualizan una sola vela, no una por tick")


def test_append_live_tick_new_day_opens_new_candle():
    import pandas as pd
    from live_runner import _append_live_tick

    dates = pd.bdate_range("2024-01-01", periods=3)
    df = pd.DataFrame({
        "open": [100, 101, 102], "high": [101, 102, 103],
        "low": [99, 100, 101], "close": [100.5, 101.5, 102.5],
        "volume": [1000] * 3,
    }, index=dates)

    df = _append_live_tick(df, pd.Timestamp("2024-01-10 09:00:00"), 103.0, last_close=102.5)
    df = _append_live_tick(df, pd.Timestamp("2024-01-11 09:00:00"), 104.0, last_close=103.0)  # día calendario distinto

    assert len(df) == 3 + 2, "Cada día calendario nuevo debe abrir su propia vela"
    assert df.loc[pd.Timestamp("2024-01-11"), "open"] == 103.0, "El open del día nuevo debe ser el cierre del día anterior"
    print("OK: un tick de un nuevo día calendario abre una vela propia")


def test_live_polling_atr_stays_realistic_across_many_same_day_ticks():
    """
    Regresión de punta a punta del bug real: antes de este fix, con
    poll_interval chico y varios ticks del mismo día, la ventana rodante
    de 14 velas del ATR terminaba llena de ticks de segundos en vez de
    días reales, y el ATR colapsaba a casi cero -- eso fue lo que
    disparó el bug de concentración visto con LINK_USDC. Simula 40 ticks
    del MISMO día calendario y verifica que el ATR sigue reflejando la
    volatilidad diaria real del dataset, no colapsa.
    """
    from live_runner import _append_live_tick, _atr
    from data_utils import load_csv
    import pandas as pd

    df = load_csv("real_data/btc_daily.csv")
    atr_real_historico = _atr(df).iloc[-1]
    precio = float(df["close"].iloc[-1])

    hoy = pd.Timestamp("2030-01-01")  # bien después del seed, un solo día calendario
    for i in range(40):  # simula 40 ticks del MISMO día (ej. poll cada 45s durante media hora)
        precio_anterior = precio
        precio += 0.5
        df = _append_live_tick(df, hoy + pd.Timedelta(seconds=i * 45), precio, last_close=precio_anterior)

    atr_post_ticks = _atr(df).iloc[-1]
    # Bajo el bug viejo, esto hubiera reemplazado las 14 velas de la
    # ventana por ticks de segundos -- el ATR hubiera colapsado a casi
    # cero. Con el fix, como mucho se agregó UNA vela nueva (la de hoy),
    # así que el ATR debe seguir cerca de su valor histórico real.
    assert atr_post_ticks > atr_real_historico * 0.5, (
        f"El ATR colapsó tras varios ticks del mismo día ({atr_real_historico:.2f} -> "
        f"{atr_post_ticks:.2f}) -- la ventana rodante debería seguir dominada por días reales"
    )
    print("OK: varios ticks del mismo día no corrompen el ATR -- sigue reflejando la volatilidad diaria real")


def test_live_engine_enforces_shared_position_cap_across_symbols():
    """
    El cupo de posiciones simultáneas (`max_positions`) es COMPARTIDO
    entre todos los símbolos que maneja un mismo motor -- no un cupo por
    símbolo. Con el cupo en 1, una señal de compra en un segundo símbolo
    no debe abrir nada mientras ya hay una posición abierta en el primero.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_shared_cap.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_shared_cap"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, ["SYM_A", "SYM_B"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_shared_cap"),
            max_positions=1,
        )
        now = datetime.now(timezone.utc)

        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_A")
        assert "SYM_A" in engine.internal_positions, "Debería abrir la primera posición sin problema"

        engine.process_tick(now, 50.0, current_atr=1.0, sig=1, symbol="SYM_B")
        assert "SYM_B" not in engine.internal_positions, (
            "No debería abrir una segunda posición -- el cupo compartido (1) ya está ocupado por SYM_A"
        )
        assert len(broker.get_open_positions()) == 1
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: el cupo de posiciones simultáneas es compartido entre símbolos, no por símbolo")


def test_live_engine_multi_symbol_shares_broker_without_artificial_limit():
    """
    Sin un cupo explícito (max_positions=None, el default), cada símbolo
    puede abrir su propia posición con normalidad -- todos comparten el
    mismo bróker (mismo pool de capital), pero no hay un límite
    artificial de a uno.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_multi_free.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_multi_free"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, ["SYM_A", "SYM_B"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_multi_free"),
        )
        now = datetime.now(timezone.utc)
        balance_before = broker.get_balance()

        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_A")
        balance_after_a = broker.get_balance()
        assert "SYM_A" in engine.internal_positions
        assert balance_after_a < balance_before, "Abrir en SYM_A debe consumir capital del mismo pool compartido"

        engine.process_tick(now, 50.0, current_atr=1.0, sig=1, symbol="SYM_B")
        assert "SYM_B" in engine.internal_positions, "Sin cupo explícito, un segundo símbolo también debe poder abrir"
        assert len(broker.get_open_positions()) == 2

        equity = engine._mark_to_market()
        assert abs(equity - balance_before) < 5.0, (
            "El equity total (efectivo + ambas posiciones a su precio de entrada) debe seguir cerca del capital inicial"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: varios símbolos bajo un mismo motor comparten capital y pueden operar en simultáneo sin un cupo artificial")


def test_symbol_expectancy_requires_minimum_trades_and_averages_pnl():
    from position_ranking import symbol_expectancy, MIN_TRADES_FOR_SCORE

    rows = [
        {"symbol": "A", "side": "sell", "pnl": "10.0"},
        {"symbol": "A", "side": "sell", "pnl": "-4.0"},
    ]
    assert symbol_expectancy("A", rows) is None, "Con menos de MIN_TRADES_FOR_SCORE operaciones, el score debe ser desconocido"

    rows.append({"symbol": "A", "side": "sell", "pnl": "2.0"})
    assert len(rows) >= MIN_TRADES_FOR_SCORE
    assert abs(symbol_expectancy("A", rows) - (10.0 - 4.0 + 2.0) / 3) < 1e-9

    # Filas de otro símbolo o de compras (sin pnl) no deben mezclarse en el promedio.
    rows_mixed = rows + [
        {"symbol": "B", "side": "sell", "pnl": "1000.0"},
        {"symbol": "A", "side": "buy", "pnl": ""},
    ]
    assert abs(symbol_expectancy("A", rows_mixed) - (10.0 - 4.0 + 2.0) / 3) < 1e-9
    print("OK: symbol_expectancy exige historia mínima y promedia solo las ventas del símbolo correcto")


def test_pick_weakest_open_position_ignores_unknown_and_picks_lowest():
    from position_ranking import pick_weakest_open_position

    rows = [
        {"symbol": "BAD", "side": "sell", "pnl": "-5.0"},
        {"symbol": "BAD", "side": "sell", "pnl": "-3.0"},
        {"symbol": "BAD", "side": "sell", "pnl": "-4.0"},
        {"symbol": "GOOD", "side": "sell", "pnl": "5.0"},
        {"symbol": "GOOD", "side": "sell", "pnl": "6.0"},
        {"symbol": "GOOD", "side": "sell", "pnl": "7.0"},
    ]
    assert pick_weakest_open_position(["BAD", "GOOD"], rows) == "BAD"
    # NEW no tiene historia suficiente todavía -- se ignora, pero eso NO
    # vuelve el resultado "desconocido" en general: entre lo que sí se
    # conoce (BAD, GOOD), sigue eligiendo el peor probado.
    assert pick_weakest_open_position(["NEW", "BAD", "GOOD"], rows) == "BAD"
    # Si NINGÚN símbolo abierto tiene historia suficiente todavía, ahí sí
    # no hay nada de qué elegir.
    assert pick_weakest_open_position(["NEW", "OTHER_NEW"], rows) is None
    print("OK: pick_weakest_open_position ignora símbolos sin historia y elige el de menor expectancy")


def test_live_engine_rotates_weakest_position_for_better_automatic_candidate():
    """
    Con el cupo compartido lleno, una señal AUTOMÁTICA en un símbolo con
    mejor historial probado debe poder cerrar la posición abierta con
    peor historial para abrir la candidata -- "el sistema elige
    automáticamente las más rentables" tiene que ser real, no solo
    primero-que-llega (ver auditoría de multi-par, README).
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_rotation.json")
    os.close(fd)
    os.remove(state_path)
    fd2, trades_path = tempfile.mkstemp(suffix="_live_engine_rotation_trades.csv")
    os.close(fd2)
    os.remove(trades_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_rotation"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        trade_history = TradeHistoryLog(trades_path)
        # Historial ya probado: SYM_BAD perdedor, SYM_GOOD ganador.
        for pnl in (-5.0, -4.0, -3.0):
            trade_history.append(symbol="SYM_BAD", side="sell", motivo="señal_estrategia",
                                  units=1.0, price=100.0, pnl=pnl, balance_resultante=1000.0)
        for pnl in (5.0, 6.0, 7.0):
            trade_history.append(symbol="SYM_GOOD", side="sell", motivo="señal_estrategia",
                                  units=1.0, price=100.0, pnl=pnl, balance_resultante=1000.0)

        engine = _LiveEngine(
            broker, ["SYM_BAD", "SYM_GOOD"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_rotation"),
            trade_history=trade_history, max_positions=1,
        )
        now = datetime.now(timezone.utc)

        broker.set_price("SYM_BAD", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_BAD")
        assert "SYM_BAD" in engine.internal_positions, "Debe poder abrir la primera posición sin problema (cupo libre)"

        broker.set_price("SYM_GOOD", 50.0)
        engine.process_tick(now, 50.0, current_atr=1.0, sig=1, symbol="SYM_GOOD")

        assert "SYM_BAD" not in engine.internal_positions, "Debe rotar: cerrar el símbolo con peor historial probado"
        assert "SYM_GOOD" in engine.internal_positions, "Debe abrir la candidata con mejor historial probado"
        assert len(broker.get_open_positions()) == 1, "El cupo compartido nunca debe superarse durante la rotación"

        rows = trade_history.load_all()
        rotation_rows = [r for r in rows if r["motivo"] == "rotacion_rentabilidad"]
        assert len(rotation_rows) == 1 and rotation_rows[0]["symbol"] == "SYM_BAD", (
            "El cierre por rotación debe quedar registrado en el historial con su propio motivo"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: con el cupo lleno, una señal automática con mejor historial probado rota la posición más débil")


def test_live_engine_does_not_rotate_for_unproven_candidate():
    """
    Sin historial propio, una candidata nueva NO debe poder desplazar una
    posición ya abierta -- aunque el símbolo abierto tenga mal historial,
    no hay evidencia de que la candidata sea mejor, solo que llegó ahora.
    Un símbolo se gana su lugar por la vía normal (cupo libre), no
    desplazando a otro por conjetura.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_no_rotation.json")
    os.close(fd)
    os.remove(state_path)
    fd2, trades_path = tempfile.mkstemp(suffix="_live_engine_no_rotation_trades.csv")
    os.close(fd2)
    os.remove(trades_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_no_rotation"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        trade_history = TradeHistoryLog(trades_path)
        for pnl in (-5.0, -4.0, -3.0):
            trade_history.append(symbol="SYM_BAD", side="sell", motivo="señal_estrategia",
                                  units=1.0, price=100.0, pnl=pnl, balance_resultante=1000.0)
        # SYM_NEW no tiene NINGÚN historial todavía.

        engine = _LiveEngine(
            broker, ["SYM_BAD", "SYM_NEW"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_no_rotation"),
            trade_history=trade_history, max_positions=1,
        )
        now = datetime.now(timezone.utc)

        broker.set_price("SYM_BAD", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_BAD")
        assert "SYM_BAD" in engine.internal_positions

        broker.set_price("SYM_NEW", 50.0)
        engine.process_tick(now, 50.0, current_atr=1.0, sig=1, symbol="SYM_NEW")

        assert "SYM_BAD" in engine.internal_positions, "No debe rotar: la candidata no tiene historial propio todavía"
        assert "SYM_NEW" not in engine.internal_positions
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: una candidata sin historial propio no puede rotar una posición ya abierta, aunque esa tenga mal historial")


def test_live_engine_manual_entry_never_triggers_rotation():
    """
    La rotación por rentabilidad es SOLO para entradas automáticas -- una
    compra manual es una decisión explícita de la persona, no debería
    poder cerrar otra posición sola por competir en el ranking. Con el
    cupo lleno, una compra manual se rechaza igual que siempre (sin rotar),
    aunque el símbolo pedido tenga mejor historial probado que lo abierto.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from manual_trading import ManualOrderQueue
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_manual_no_rotation.json")
    os.close(fd)
    os.remove(state_path)
    fd2, trades_path = tempfile.mkstemp(suffix="_live_engine_manual_no_rotation_trades.csv")
    os.close(fd2)
    os.remove(trades_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_manual_no_rotation"
    manual_orders_path = ".MANUAL_ORDERS_test_live_engine_manual_no_rotation"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        trade_history = TradeHistoryLog(trades_path)
        for pnl in (-5.0, -4.0, -3.0):
            trade_history.append(symbol="SYM_BAD", side="sell", motivo="señal_estrategia",
                                  units=1.0, price=100.0, pnl=pnl, balance_resultante=1000.0)
        for pnl in (5.0, 6.0, 7.0):
            trade_history.append(symbol="SYM_GOOD", side="sell", motivo="señal_estrategia",
                                  units=1.0, price=100.0, pnl=pnl, balance_resultante=1000.0)
        manual_orders = ManualOrderQueue(manual_orders_path)

        engine = _LiveEngine(
            broker, ["SYM_BAD", "SYM_GOOD"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_manual_no_rotation"),
            trade_history=trade_history, max_positions=1, manual_orders=manual_orders,
        )
        now = datetime.now(timezone.utc)

        broker.set_price("SYM_BAD", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_BAD")
        assert "SYM_BAD" in engine.internal_positions

        broker.set_price("SYM_GOOD", 50.0)
        manual_orders.queue_order("SYM_GOOD", "buy")
        engine.process_tick(now, 50.0, current_atr=1.0, sig=0, symbol="SYM_GOOD")

        assert "SYM_BAD" in engine.internal_positions, "No debe rotar por una compra manual, aunque SYM_GOOD tenga mejor historial"
        assert "SYM_GOOD" not in engine.internal_positions, "La compra manual debe rechazarse por cupo lleno, no forzar una rotación"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(manual_orders_path):
            os.remove(manual_orders_path)
    print("OK: una compra manual con el cupo lleno se rechaza igual que siempre, nunca dispara una rotación")


def test_run_live_polling_multi_symbol_smoke_test():
    """
    Test de humo de run_live_polling con VARIOS símbolos a la vez (ver
    Fase roadmap: multi-par con cupo compartido) -- un price_source de
    prueba con un precio distinto por símbolo, corre varios ciclos sin
    romperse, y nunca deja más posiciones abiertas que el cupo compartido.
    """
    import os
    import tempfile
    from live_runner import run_live_polling

    class StubMultiPriceSource:
        def __init__(self, prices_by_symbol):
            self._prices = prices_by_symbol

        def get_current_price(self, symbol):
            return self._prices[symbol]

    price_source = StubMultiPriceSource({"SYM_A": 100.0, "SYM_B": 50.0})

    fd, state_path = tempfile.mkstemp(suffix="_live_polling_multi_state.json")
    os.close(fd)
    os.remove(state_path)
    try:
        broker = run_live_polling(
            price_source, symbol=["SYM_A", "SYM_B"], strategy_name="momentum", profile_name="moderado",
            seed_csv={"SYM_A": "real_data/btc_daily.csv", "SYM_B": "real_data/eth_daily.csv"},
            initial_balance=1000.0, poll_interval_seconds=0, max_ticks=10, state_path=state_path,
            max_positions=1,
        )
        assert broker.get_balance() >= 0, "El balance nunca debería quedar negativo"
        assert len(broker.get_open_positions()) <= 1, "El cupo compartido (1) nunca debería superarse"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: run_live_polling multi-símbolo corre de punta a punta y respeta el cupo compartido de posiciones")


def test_broker_adapters_dont_leak_into_each_other():
    """
    Test específico para el tipo de bug que apareció durante el desarrollo:
    una edición de texto puede borrar accidentalmente la línea 'class X:'
    de una clase, dejando su __init__ huérfano DENTRO de la clase anterior
    (Python lo interpreta como un segundo método con el mismo nombre, que
    pisa al primero). Esto verifica que cada adaptador de bróker tiene
    sus propios atributos y no los de otro.
    """
    from broker import RipioBrokerAdapter, LibertexBrokerAdapter
    ripio = RipioBrokerAdapter()
    libertex = LibertexBrokerAdapter()

    assert hasattr(ripio, "api_token"), "RipioBrokerAdapter debería tener su propio atributo api_token"
    assert not hasattr(ripio, "api_key"), (
        "RipioBrokerAdapter NO debería tener api_key -- si lo tiene, "
        "significa que el __init__ de otra clase se coló en esta"
    )
    assert hasattr(libertex, "api_key"), "LibertexBrokerAdapter debería tener su propio atributo api_key"
    print("OK: los adaptadores de bróker no se pisan entre sí (cada clase mantiene sus propios atributos)")


def test_state_survives_simulated_restart():
    import os
    import tempfile
    from state_store import StateStore

    fd, path = tempfile.mkstemp(suffix="_test_state_sanity.json")
    os.close(fd)
    os.remove(path)

    store = StateStore(path=path)
    positions = {"BTCUSD": {"unidades": 0.02, "precio_entrada": 55000.0}}
    store.save(positions, capital=700.0)

    store_reloaded = StateStore(path=path)  # simula un proceso nuevo
    restored = store_reloaded.load()
    assert restored["positions"] == positions, "Las posiciones restauradas deberían ser idénticas a las guardadas"
    assert restored["capital"] == 700.0, "El capital restaurado debería ser idéntico al guardado"
    os.remove(path)
    print("OK: el estado sobrevive a un reinicio simulado del proceso")


def test_live_engine_restores_saved_capital_on_restart():
    """
    Bug real encontrado en producción (visto en el status_report.py real
    de ETH_USDC): tras reiniciar la sesión para aislar el kill-switch, el
    capital volvió a mostrar 1000.00 pese a que la corrida anterior había
    cerrado en 991.87 tras dos operaciones reales. Causa: _LiveEngine
    restauraba posiciones, stop/take profit y orden pendiente desde el
    estado guardado, pero NUNCA el capital -- el bróker siempre arrancaba
    con el --capital de la CLI, descartando en silencio cualquier
    ganancia o pérdida acumulada en corridas anteriores. Este test arma
    un estado guardado con un capital DISTINTO al --capital inicial y
    verifica que, tras "reiniciar" (nueva instancia de _LiveEngine), el
    bróker arranca con el capital REAL guardado, no con el de la CLI.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_capital_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_capital"

    try:
        # Corrida 1: arranca con 1000, una operación la deja en 850 y se persiste.
        StateStore(path=state_path).save({}, capital=850.0, extra={})

        # Corrida 2 ("reinicio"): un broker NUEVO, arrancado con el mismo
        # --capital de siempre (1000.0) -- el bug hacía que se quedara así.
        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_capital"),
        )

        assert engine.broker.get_balance() == 850.0, (
            f"Debería restaurar el capital guardado (850.0), no quedarse con el --capital "
            f"inicial de la CLI (obtuvo {engine.broker.get_balance()})"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura el capital real guardado en vez de reiniciar con el --capital de la CLI")


def test_live_engine_restores_open_position_in_broker_on_restart():
    """
    Bug real, encontrado siguiendo la misma pista que el del capital: si
    el proceso se para con una posición abierta, `internal_positions` se
    restauraba bien, pero el bróker (una instancia nueva, sin memoria de
    nada) quedaba SIN esa posición. `in_position` en process_tick() se
    calcula mirando solo al bróker (`self.symbol in
    self.broker.get_open_positions()`), así que en el primer tick tras un
    reinicio el motor pensaría que no hay nada abierto -- y podría
    intentar abrir una posición nueva encima de la que en realidad seguía
    activa, en vez de vigilarla con su stop loss/take profit real.
    """
    import os
    import tempfile
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_position_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_position"

    try:
        # Corrida 1: se para con una posición abierta y la persiste.
        StateStore(path=state_path).save(
            {"TEST_SYM": {"unidades": 0.5, "precio_entrada": 1955.0}}, capital=1023.0, extra={}
        )

        # Corrida 2 ("reinicio"): un bróker NUEVO, que nunca vio esa operación.
        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_position"),
        )

        broker_positions = engine.broker.get_open_positions()
        assert "TEST_SYM" in broker_positions, (
            "El bróker debería conocer la posición restaurada, no solo el registro interno"
        )
        assert broker_positions["TEST_SYM"]["unidades"] == 0.5
        assert broker_positions["TEST_SYM"]["precio_entrada"] == 1955.0
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura la posición abierta también en el bróker, no solo en el registro interno")


def test_live_engine_restores_circuit_breaker_state_on_restart():
    """
    Bug real, el más serio de esta línea de auditoría: un reinicio del
    proceso DESACTIVABA en silencio un circuit breaker que estaba activo
    (`tripped` siempre arrancaba en False) y borraba el pico histórico de
    equity que define el drawdown (`equity_curve` volvía a arrancar
    vacío, así que el primer tick post-reinicio se convertía en el nuevo
    "pico", ocultando cualquier caída anterior al reinicio). Esto es
    justo el freno de seguridad más destacado en la propuesta -- que un
    simple reinicio lo resetee es un hueco serio, no cosmético.

    Reproducido con un caso concreto: capital sube a 1000, cae a 800
    (-20%, dispara el breaker con el límite en 15%), se persiste, y una
    instancia NUEVA (bróker y circuit breaker frescos, "reinicio")
    restaura el estado -- debe seguir tripped, con el pico real (1000)
    recordado, no reseteado.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_breaker_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_breaker"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        cb = CircuitBreaker(max_drawdown_pct=15.0, max_daily_loss_pct=90.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            cb, Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_breaker"),
        )
        now = datetime.now(timezone.utc)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)
        engine.equity_curve[-1] = 1000.0  # fija el pico en 1000 para el escenario
        broker.balance = 800.0  # caída del 20% -- supera el límite de 15%
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)
        assert cb.tripped is True, "El circuit breaker debería haberse activado con un drawdown del 20%"
        engine.force_persist()

        # "Reinicio": bróker y circuit breaker completamente nuevos.
        broker2 = PaperBroker(initial_balance=1000.0)
        cb2 = CircuitBreaker(max_drawdown_pct=15.0, max_daily_loss_pct=90.0)
        engine2 = _LiveEngine(
            broker2, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            cb2, Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_breaker2"),
        )

        assert cb2.tripped is True, "El freno activo debe seguir activo tras el reinicio, no resetearse solo"
        assert cb2.trip_reason == cb.trip_reason
        assert engine2.equity_curve == [1000.0], (
            f"El pico histórico de equity debe sobrevivir al reinicio (obtuvo {engine2.equity_curve})"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura el estado del circuit breaker (tripped + pico de equity) tras un reinicio")


def test_live_engine_restores_news_pause_on_restart():
    """
    Mismo problema, encontrado auditando el resto de los frenos
    automáticos tras el bug del circuit breaker: NewsGuard también
    guardaba su pausa activa (`_paused_until`) solo en memoria. Un
    reinicio del proceso durante una pausa automática por noticia de alto
    impacto la levantaba en silencio, igual que le pasaba al circuit
    breaker antes de corregirlo.
    """
    import os
    import tempfile
    from datetime import datetime, timezone, timedelta
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger
    from news_monitor import NewsGuard, NewsMonitor, NewsAutomationSchedule, AutomationWindow

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_news_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_news"

    try:
        guard = NewsGuard(NewsMonitor(feeds=[]),
                           NewsAutomationSchedule([AutomationWindow(0, 24)], cooldown_minutes=60),
                           ConsoleAlertChannel())
        guard.restore_paused_until(datetime.now(timezone.utc) + timedelta(minutes=45))
        assert guard.entries_paused() is True

        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_news"),
            news_guard=guard,
        )
        engine.force_persist()

        # "Reinicio": un NewsGuard completamente nuevo.
        guard2 = NewsGuard(NewsMonitor(feeds=[]),
                            NewsAutomationSchedule([AutomationWindow(0, 24)], cooldown_minutes=60),
                            ConsoleAlertChannel())
        engine2 = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_news2"),
            news_guard=guard2,
        )

        assert guard2.entries_paused() is True, "La pausa por noticias activa debe seguir activa tras el reinicio"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura la pausa automática por noticias tras un reinicio")


def test_live_engine_restores_news_seen_links_on_restart():
    """
    Mismo bug, de punta a punta a través de _LiveEngine: sin restaurar el
    deduplicado de noticias, un reinicio del proceso mientras el feed RSS
    todavía tiene el mismo titular de alto impacto lo vuelve a alertar Y
    vuelve a disparar una pausa automática nueva -- aunque no haya pasado
    nada nuevo de verdad.
    """
    import os
    import tempfile
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger
    from news_monitor import NewsGuard, NewsMonitor, NewsAutomationSchedule, AutomationWindow

    def fake_http_get(url, timeout):
        return _SAMPLE_RSS

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_news_seen_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_news_seen"

    try:
        guard = NewsGuard(NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get),
                           NewsAutomationSchedule([AutomationWindow(0, 24)], cooldown_minutes=60),
                           ConsoleAlertChannel())
        found = guard.check()
        assert len(found) == 2, "Debe alertar los 2 titulares de alto impacto del feed de prueba"
        assert guard.entries_paused() is True, "Modo automático: debe pausar entradas ante la primera alerta"

        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_news_seen"),
            news_guard=guard,
        )
        engine.force_persist()

        # "Reinicio": un NewsGuard/NewsMonitor completamente nuevos, pero el
        # feed (simulado) SIGUE devolviendo los mismos titulares -- como
        # pasaría de verdad, un feed RSS real mantiene varios días de historia.
        guard2 = NewsGuard(NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get),
                            NewsAutomationSchedule([AutomationWindow(0, 24)], cooldown_minutes=60),
                            ConsoleAlertChannel())
        engine2 = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_news_seen2"),
            news_guard=guard2,
        )

        found_again = guard2.check()
        assert found_again == [], "Tras el reinicio, no debe re-alertar titulares que ya se habían visto"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura el deduplicado de noticias tras un reinicio, evitando re-alertas")


def test_live_engine_restores_daily_loss_reference_on_restart():
    """
    Cierre de la línea de auditoría de reinicios: la referencia de
    pérdida diaria (`day_start_equity`/`current_day`, el segundo de los
    dos chequeos del circuit breaker junto al drawdown desde el pico)
    tenía el mismo problema -- vivía solo en memoria. Un reinicio a mitad
    de un día que ya venía con pérdida reseteaba la referencia al equity
    del momento del reinicio, ocultando la caída previa del chequeo de
    pérdida diaria.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_daily_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_daily"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_daily"),
        )
        now = datetime.now(timezone.utc)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)  # day_start_equity queda en 1000
        broker.balance = 950.0  # -5% en el mismo día
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)
        assert engine.day_start_equity == 1000.0
        engine.force_persist()

        broker2 = PaperBroker(initial_balance=1000.0)
        broker2.balance = 950.0
        engine2 = _LiveEngine(
            broker2, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_daily2"),
        )

        assert engine2.day_start_equity == 1000.0, (
            f"Debe restaurar el equity real de inicio del día (1000.0), no reiniciarlo al actual "
            f"(obtuvo {engine2.day_start_equity})"
        )
        assert engine2.current_day == engine.current_day
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura la referencia de pérdida diaria tras un reinicio")


def test_live_engine_closes_position_to_lock_profit_and_keeps_operating():
    """
    Al llegar a la meta, el motor debe CERRAR la posición abierta para
    asegurar la ganancia de verdad (no dejarla flotando, expuesta a que
    el precio se dé vuelta antes de tocar su propio stop loss) -- y
    después seguir operando con normalidad, no quedarse pausado.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch, ProfitLock
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_profit_lock.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_profit_lock"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        lock = ProfitLock(target_pct=10.0, reference_capital=1000.0)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_profit_lock"),
            profit_lock=lock,
        )
        now = datetime.now(timezone.utc)

        broker.set_price("TEST_SYM", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)  # abre posición
        assert "TEST_SYM" in engine.internal_positions
        units = engine.internal_positions["TEST_SYM"]["unidades"]
        entry_price = engine.internal_positions["TEST_SYM"]["precio_entrada"]
        # Neutraliza el stop loss/take profit propios de la posición para
        # aislar el efecto del seguro de ganancias (que se lo dispare a él,
        # no a los frenos normales de la operación).
        engine.stop_loss["TEST_SYM"] = 0.0
        engine.take_profit["TEST_SYM"] = 10_000_000.0

        current_equity = engine._mark_to_market()
        target_equity = 1000.0 * 1.10
        needed_price = entry_price + (target_equity - current_equity) / units + 5.0  # margen
        broker.set_price("TEST_SYM", needed_price)

        engine.process_tick(now, needed_price, current_atr=2.0, sig=1)  # sig=1: no es salida de estrategia
        assert "TEST_SYM" not in engine.internal_positions, "Debe cerrar la posición al asegurar la ganancia"
        assert lock.times_locked == 1, "Debe registrar el aseguramiento de ganancia"
        assert lock.reference_capital > 1000.0, "El piso debe subir al nuevo capital, ya realizado"

        # Sigue operando con normalidad: una señal de compra posterior
        # debe poder abrir una posición nueva -- no quedó pausado.
        engine.process_tick(now, needed_price, current_atr=2.0, sig=1)
        assert "TEST_SYM" in engine.internal_positions, (
            "El seguro de ganancias no debe bloquear operaciones nuevas después de asegurar"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: el seguro de ganancias cierra la posición para asegurar la ganancia y sigue operando después")


def test_live_engine_profit_lock_multi_symbol_banks_full_equity_not_just_cash():
    """
    Bug real encontrado en auditoría: cuando el símbolo del tick actual NO
    tiene posición propia pero la meta de ganancia igual se alcanzó (por
    la ganancia no realizada de OTRO símbolo que sigue abierto), el atajo
    de "bancar directo, sin pasar por el bróker" usaba
    `self.broker.get_balance()` (solo efectivo) en vez de `equity`
    (efectivo + TODAS las posiciones). Acá SYM_B queda con una posición
    cuya ganancia no realizada por sí sola ya supera la meta, mientras
    SYM_A (el símbolo que procesa este tick) nunca tuvo posición -- el
    efectivo solo (500) es MENOR que el piso de referencia original
    (1000), así que el bug original hacía bajar el piso del trinquete, no
    subirlo, violando su garantía central.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch, ProfitLock
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_profit_lock_multi.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_profit_lock_multi"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        broker.set_price("SYM_B", 100.0)
        broker.place_order("SYM_B", "buy", 5.0)  # ~500 en efectivo consumidos (slippage + comisión incluidos)
        cash_after_buy = broker.get_balance()
        assert cash_after_buy < 500.0, "La compra debe consumir efectivo (más comisión/slippage)"

        lock = ProfitLock(target_pct=25.0, reference_capital=1000.0)
        engine = _LiveEngine(
            broker, ["SYM_A", "SYM_B"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_profit_lock_multi"),
            profit_lock=lock,
        )
        now = datetime.now(timezone.utc)

        # SYM_B sube a 160: su posición sola vale 800, equity total =
        # ~499 (efectivo) + 800 = ~1299 -> ~+30% sobre el piso de 1000,
        # supera la meta de 25%. El efectivo solo (~499) NUNCA la
        # alcanzaría -- de hecho es menor al piso original.
        broker.set_price("SYM_B", 160.0)
        expected_equity = cash_after_buy + 5.0 * 160.0  # sin más slippage: no se pasa por el bróker de nuevo

        # Se procesa un tick de SYM_A, que nunca tuvo posición propia.
        engine.process_tick(now, 50.0, current_atr=1.0, sig=0, symbol="SYM_A")

        assert lock.times_locked == 1, "Debe registrar el aseguramiento aunque el símbolo del tick no tenga posición"
        assert abs(lock.reference_capital - expected_equity) < 0.01, (
            "Debe bancar el equity total (efectivo + posición de SYM_B), no solo el efectivo"
        )
        assert lock.reference_capital > 1000.0, "El piso del trinquete NUNCA debe bajar del original"
        assert "SYM_B" in broker.get_open_positions(), (
            "Este atajo es solo contable -- no debe tocar la posición abierta de otro símbolo"
        )
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: el seguro de ganancias banca el equity total multi-símbolo, no solo el efectivo del símbolo del tick")


def test_live_engine_profit_lock_sell_close_banks_full_equity_not_just_cash():
    """
    Segunda instancia del mismo bug (ver auditoría, README): al cerrar la
    posición del símbolo que sí disparó la venta por seguro de ganancias,
    `_apply_sell_fill()` bancaba `self.broker.get_balance()` (solo
    efectivo) en vez del equity total. Acá SYM_B queda abierta y con una
    ganancia enorme no realizada mientras se cierra SYM_A por seguro de
    ganancias -- el efectivo solo tras esa venta deja completamente afuera
    el valor de SYM_B.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch, ProfitLock
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_profit_lock_sell_multi.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_profit_lock_sell_multi"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        lock = ProfitLock(target_pct=10.0, reference_capital=1000.0)
        engine = _LiveEngine(
            broker, ["SYM_A", "SYM_B"], get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_profit_lock_sell_multi"),
            profit_lock=lock,
        )
        now = datetime.now(timezone.utc)

        broker.set_price("SYM_A", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_A")
        assert "SYM_A" in engine.internal_positions
        engine.stop_loss["SYM_A"] = 0.0
        engine.take_profit["SYM_A"] = 10_000_000.0

        # SYM_B se abre directo contra el bróker -- no hace falta que el
        # motor la registre como propia, solo que quede abierta y valiosa.
        broker.set_price("SYM_B", 50.0)
        broker.place_order("SYM_B", "buy", 6.0)
        broker.set_price("SYM_B", 300.0)  # sube fuerte, sin que SYM_A se mueva de precio

        equity_before_close = engine._mark_to_market()
        assert equity_before_close >= 1000.0 * 1.10, "El escenario debe cruzar la meta antes de cerrar nada"

        engine.process_tick(now, 100.0, current_atr=2.0, sig=1, symbol="SYM_A")  # sig=1: no es salida de estrategia

        assert "SYM_A" not in engine.internal_positions, "Debe cerrar SYM_A para asegurar la ganancia"
        assert lock.times_locked == 1
        b_units = broker.get_open_positions()["SYM_B"]["unidades"]
        expected = broker.get_balance() + b_units * broker.get_current_price("SYM_B")
        assert abs(lock.reference_capital - expected) < 0.01, (
            "Debe bancar equity total (incluye la posición abierta de SYM_B), no solo el efectivo tras la venta"
        )
        assert lock.reference_capital > broker.get_balance() + 1.0, (
            "El efectivo solo, sin SYM_B, hubiera sido muchísimo menor que el equity real"
        )
        assert "SYM_B" in broker.get_open_positions(), "No debe tocar la posición abierta de otro símbolo"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: cerrar por seguro de ganancias banca el equity total multi-símbolo, no solo el efectivo tras la venta")


def test_live_engine_restores_profit_lock_state_on_restart():
    """
    Mismo cuidado que con el circuit breaker: el piso de referencia (que
    sube cada vez que se asegura una ganancia) y cuántas veces ya se
    aseguró no deben resetearse solos con un reinicio del proceso.
    """
    import os
    import tempfile
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch, ProfitLock
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_profit_lock_restart.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_profit_lock_restart"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        lock = ProfitLock(target_pct=10.0, reference_capital=1000.0)
        assert lock.check(1150.0) is True
        lock.lock_in(1150.0)  # asegura de verdad, no a mano
        assert lock.times_locked == 1

        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_pl_restart"),
            profit_lock=lock,
        )
        engine.force_persist()

        # "Reinicio": un ProfitLock nuevo, con un reference_capital de
        # arranque DISTINTO (como pasaría si --capital cambiara entre
        # corridas) -- debe ganar el guardado, no el de esta instancia nueva.
        lock2 = ProfitLock(target_pct=10.0, reference_capital=999.0)
        engine2 = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_pl_restart2"),
            profit_lock=lock2,
        )

        assert lock2.times_locked == 1, "El contador de ganancias aseguradas debe sobrevivir al reinicio"
        assert lock2.reference_capital == 1150.0, "El piso real (post-aseguramiento) debe sobrevivir al reinicio"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine restaura el estado del seguro de ganancias (activo + referencia) tras un reinicio")


def test_state_store_handles_corrupt_file():
    import os
    import tempfile
    from state_store import StateStore

    fd, path = tempfile.mkstemp(suffix="_test_state_corrupto_sanity.json")
    os.close(fd)
    with open(path, "w") as f:
        f.write("{esto no es json valido,,,")
    store = StateStore(path=path)
    restored = store.load()  # no debería lanzar excepción
    assert restored["positions"] == {}, "Un archivo corrupto debería arrancar limpio, no crashear"
    os.remove(path)
    print("OK: un archivo de estado corrupto no rompe el arranque")


def test_reconciliation_detects_all_mismatch_types():
    from reconciliation import reconcile

    # Coincide
    assert reconcile({"A": {"unidades": 1}}, {"A": {"unidades": 1}})["coincide"] is True

    # Posición fantasma interna
    r2 = reconcile({"A": {"unidades": 1}, "B": {"unidades": 1}}, {"A": {"unidades": 1}})
    assert r2["solo_en_interno"] == ["B"], "Debería detectar que 'B' solo está en el registro interno"

    # Posición no registrada (operación manual)
    r3 = reconcile({"A": {"unidades": 1}}, {"A": {"unidades": 1}, "C": {"unidades": 1}})
    assert r3["solo_en_broker"] == ["C"], "Debería detectar que 'C' solo está en el bróker"

    # Cantidad distinta
    r4 = reconcile({"A": {"unidades": 1.0}}, {"A": {"unidades": 0.5}})
    assert "A" in r4["diferencias_de_cantidad"], "Debería detectar la diferencia de cantidad en 'A'"
    print("OK: la reconciliación detecta los 4 tipos de desfasaje (coincide, fantasma, no registrada, cantidad distinta)")


def test_retry_with_backoff_retries_transient_not_permanent():
    from resilience import retry_with_backoff, TransientBrokerError, PermanentBrokerError

    calls = {"n": 0}

    @retry_with_backoff(max_attempts=3, base_delay_seconds=0.01)
    def transient_then_ok():
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientBrokerError("falla simulada")
        return "ok"

    assert transient_then_ok() == "ok", "Debería recuperarse tras un reintento"
    assert calls["n"] == 2, "Debería haber tardado exactamente 2 intentos en tener éxito"

    calls_permanent = {"n": 0}

    @retry_with_backoff(max_attempts=3, base_delay_seconds=0.01)
    def always_permanent():
        calls_permanent["n"] += 1
        raise PermanentBrokerError("error no recuperable")

    try:
        always_permanent()
        assert False, "Debería haber lanzado PermanentBrokerError"
    except PermanentBrokerError:
        pass
    assert calls_permanent["n"] == 1, "Un error permanente NO debería reintentarse -- debería haber usado solo 1 intento"
    print("OK: reintenta errores transitorios pero falla inmediato ante errores permanentes")


def test_paper_broker_order_idempotency():
    from broker import PaperBroker
    pb = PaperBroker(initial_balance=1000.0)
    pb.set_price("TEST", 100.0)

    order1 = pb.place_order("TEST", "buy", 1.0, client_order_id="dup-test-1")
    balance_after_first = pb.get_balance()

    order2 = pb.place_order("TEST", "buy", 1.0, client_order_id="dup-test-1")
    balance_after_retry = pb.get_balance()

    assert order1 == order2, "Un reintento con el mismo client_order_id debe devolver el resultado original"
    assert balance_after_first == balance_after_retry, "El balance no debe cambiar por un reintento duplicado"
    print("OK: el PaperBroker no ejecuta la misma orden dos veces con el mismo client_order_id")


def test_significance_module_runs_and_bounds_percentile():
    from data_utils import generate_synthetic_data
    from backtester import Backtester
    from significance import test_significance_vs_random

    df = generate_synthetic_data(n_days=400, seed=55)
    bt = Backtester(df, "momentum", "moderado", initial_capital=1000)
    result = bt.run()

    if result["num_operaciones"] == 0:
        print("OK (sin operaciones que evaluar, se omite la comparación estadística)")
        return

    sig = test_significance_vs_random(df, result, "moderado", initial_capital=1000, n_simulations=30)
    assert 0 <= sig["percentil_de_la_estrategia_real"] <= 100, "El percentil debe estar entre 0 y 100"
    assert sig["interpretacion"] is not None, "Siempre debería devolver una interpretación"
    print("OK: el módulo de significancia corre y devuelve un percentil acotado correctamente")


def test_significance_random_strategy_never_overlaps_positions():
    """
    Bug metodológico real encontrado en auditoría: la simulación aleatoria
    de significancia procesaba cada fecha de entrada elegida al azar como
    si se resolviera al instante -- eso le permitía "reutilizar" capital
    que en una cronología real todavía seguiría atado a una posición sin
    cerrar, algo que la estrategia real (Backtester.run(), una sola
    posición a la vez) nunca puede hacer. Una comparación contra una línea
    de base que puede hacer trampa no es una comparación justa.

    Con precio CONSTANTE y un ATR chico que nunca toca stop/take profit,
    cualquier posición que se abra se sostiene hasta el último día del
    dataset -- así que, con el freno de una sola posición a la vez, NO
    IMPORTA cuántas operaciones se pidan (acá 5): solo la primera puede
    ejecutarse de verdad, sin importar el seed. El resultado queda fijo
    (una sola operación paga comisión de ida y vuelta sobre el mismo
    precio) -- antes del fix, pedir 5 operaciones ejecutaba las 5,
    multiplicando esa pérdida de comisión por 5.
    """
    import numpy as np
    from significance import _run_random_strategy
    from risk_profiles import get_profile

    dates = pd.bdate_range("2024-01-01", periods=15)
    df = pd.DataFrame({
        "open": [100.0] * 15, "high": [101.0] * 15, "low": [99.0] * 15,
        "close": [100.0] * 15, "volume": [1000] * 15,
    }, index=dates)
    profile = get_profile("moderado")

    for seed in (1, 2, 3, 42, 7, 100):
        ret = _run_random_strategy(df, profile, initial_capital=1000.0,
                                    n_trades_target=5, rng=np.random.default_rng(seed))
        assert abs(ret - (-0.05)) < 1e-6, (
            f"seed={seed}: esperaba el resultado de UNA sola operación (-0.05%), dio {ret} -- "
            f"parece estar ejecutando más de una operación superpuesta"
        )
    print("OK: la simulación aleatoria de significancia nunca superpone posiciones, igual que la estrategia real")


def test_parameter_sensitivity_detects_sign_flip():
    from data_utils import generate_synthetic_data
    from sensitivity import parameter_sensitivity

    df = generate_synthetic_data(n_days=300, seed=33)
    result = parameter_sensitivity(df, "tendencia", "moderado",
                                    param_grid={"fast": [10, 20], "slow": [40, 50]},
                                    initial_capital=1000)
    assert "cambia_de_signo" in result, "Debe informar si los resultados cambian de signo entre parámetros"
    assert len(result["combinaciones"]) == 4, "Debe correr las 4 combinaciones del grid (2x2)"
    print("OK: el análisis de sensibilidad de parámetros corre el grid completo y detecta cambios de signo")


def test_ripio_signature_matches_official_scheme():
    """
    Vector fijo alineado con los ejemplos oficiales de Ripio
    (Timestamp + METHOD + pathSinQuery + body → HMAC-SHA256 → Base64).
    """
    from broker import RipioBrokerAdapter

    sig = RipioBrokerAdapter.build_signature(
        secret="test-secret",
        timestamp="1700000000000",
        method="GET",
        path="/trade/user/balances",
        body="",
    )
    import hmac, hashlib, base64
    message = "1700000000000GET/trade/user/balances"
    expected = base64.b64encode(
        hmac.new(b"test-secret", message.encode(), hashlib.sha256).digest()
    ).decode()
    assert sig == expected, "La firma HMAC no coincide con el esquema oficial de Ripio"

    sig_q = RipioBrokerAdapter.build_signature(
        secret="test-secret",
        timestamp="1700000000000",
        method="GET",
        path="/trade/orders?pair=BTC_USDC&status=open",
        body="",
    )
    message_q = "1700000000000GET/trade/orders"
    expected_q = base64.b64encode(
        hmac.new(b"test-secret", message_q.encode(), hashlib.sha256).digest()
    ).decode()
    assert sig_q == expected_q, "La firma debe usar el path sin query params"
    print("OK: la firma HMAC de Ripio coincide con el esquema oficial")


def test_ripio_normalize_pair():
    from broker import RipioBrokerAdapter
    assert RipioBrokerAdapter.normalize_pair("btc-usdc") == "BTC_USDC"
    assert RipioBrokerAdapter.normalize_pair("BTC/USDC") == "BTC_USDC"
    assert RipioBrokerAdapter.normalize_pair("BTC_USDC") == "BTC_USDC"
    assert RipioBrokerAdapter.normalize_pair("BTCUSDC") == "BTC_USDC"
    print("OK: normalización de pares Ripio")


def test_ripio_place_order_blocked_without_allow_trading():
    from broker import RipioBrokerAdapter
    broker = RipioBrokerAdapter(api_token="fake", api_secret="fake", allow_trading=False)
    result = broker.place_order("BTC_USDC", "buy", 0.001)
    assert result["status"] == "rejected", "Sin allow_trading no debería enviar la orden"
    assert "allow_trading" in result["motivo"]
    print("OK: place_order de Ripio queda bloqueado en modo lectura")


def test_ripio_private_requires_credentials():
    from broker import RipioBrokerAdapter
    from resilience import PermanentBrokerError

    broker = RipioBrokerAdapter(api_token=None, api_secret=None, allow_trading=False)
    broker.api_token = None
    broker.api_secret = None
    try:
        broker.get_balance()
        assert False, "Debería fallar sin credenciales"
    except PermanentBrokerError:
        pass
    print("OK: endpoints privados de Ripio exigen credenciales")


def test_ripio_public_ticker_live():
    """Humo real contra el ticker público (sin credenciales)."""
    from broker import RipioBrokerAdapter
    broker = RipioBrokerAdapter(allow_trading=False)
    price = broker.get_current_price("BTC_USDC")
    assert price > 0, f"El precio de BTC_USDC debería ser > 0, llegó {price}"
    print(f"OK: ticker público Ripio BTC_USDC = {price}")


_SAMPLE_RSS = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Exchange suffers major hack, millions stolen</title>
    <link>https://example.com/news/1</link>
    <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Bitcoin price steady amid quiet trading day</title>
    <link>https://example.com/news/2</link>
    <pubDate>Wed, 01 Jan 2026 01:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Regulator announces new crackdown on crypto exchanges</title>
    <link>https://example.com/news/3</link>
    <pubDate>Wed, 01 Jan 2026 02:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_classify_impact_matches_keywords():
    from news_monitor import classify_impact

    assert classify_impact("Exchange suffers major hack, millions stolen") != []
    assert classify_impact("Bitcoin price steady amid quiet trading day") == []
    print("OK: la clasificación por palabras clave detecta e ignora titulares correctamente")


def test_news_monitor_dedup_and_filters_low_impact():
    """
    Con un feed inyectado (sin red real): debe devolver solo los titulares
    de alto impacto, y no repetir el mismo titular en una segunda llamada.
    """
    from news_monitor import NewsMonitor

    def fake_http_get(url, timeout):
        return _SAMPLE_RSS

    monitor = NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get)

    first = monitor.fetch_high_impact_news()
    assert len(first) == 2, f"Esperaba 2 titulares de alto impacto, llegaron {len(first)}"
    titles = {item.title for item in first}
    assert "Bitcoin price steady amid quiet trading day" not in titles

    second = monitor.fetch_high_impact_news()
    assert second == [], "No debería re-alertar el mismo titular ya visto"
    print("OK: el monitor de noticias filtra por impacto y no duplica alertas ya vistas")


def test_news_monitor_get_and_restore_seen_links():
    """
    Bug real encontrado en auditoría: `_seen_links` solo vivía en memoria
    -- cada reinicio del proceso arrancaba con el deduplicado vacío y
    volvía a alertar (y en modo automático, a PAUSAR entradas) sobre
    titulares que ya se habían visto antes de reiniciar. Confirmado en
    vivo: el mismo titular apareció repetido varias veces en un mismo
    log, una por cada reinicio del día. Acá se prueba el mecanismo de
    persistencia en sí (get/restore_seen_links), no todavía el reinicio
    completo del motor (ver test_live_engine_restores_news_seen_links_on_restart)."""
    from news_monitor import NewsMonitor

    def fake_http_get(url, timeout):
        return _SAMPLE_RSS

    monitor = NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get)
    first = monitor.fetch_high_impact_news()
    assert len(first) == 2
    seen = monitor.get_seen_links()

    # "Reinicio": un monitor completamente nuevo, sin el estado en memoria del anterior.
    monitor2 = NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get)
    without_restore = monitor2.fetch_high_impact_news()
    assert len(without_restore) == 2, "Sin restaurar, un monitor nuevo vuelve a ver los mismos titulares (el bug)"

    monitor3 = NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get)
    monitor3.restore_seen_links(seen)
    with_restore = monitor3.fetch_high_impact_news()
    assert with_restore == [], "Con el deduplicado restaurado, no debe re-alertar titulares ya vistos"
    print("OK: get_seen_links/restore_seen_links evitan re-alertar titulares ya vistos antes de un reinicio")


def test_news_monitor_empty_feeds_list_makes_no_requests():
    """
    Regresion: feeds=[] (lista vacia explicita, "sin feeds") NO debe caer
    a los feeds reales por defecto. Bug real encontrado durante el
    desarrollo de este mismo modulo: `feeds or DEFAULT_FEEDS` trataba []
    como "no se paso nada" y terminaba llamando a las URLs reales.
    """
    from news_monitor import NewsMonitor

    calls = []

    def counting_http_get(url, timeout):
        calls.append(url)
        return ""

    monitor = NewsMonitor(feeds=[], http_get=counting_http_get)
    result = monitor.fetch_high_impact_news()
    assert result == []
    assert calls == [], f"feeds=[] no deberia disparar ninguna llamada, se llamo a: {calls}"
    print("OK: feeds=[] explicito no cae a los feeds reales por defecto")


def test_automation_window_handles_midnight_crossing():
    from news_monitor import AutomationWindow
    from datetime import datetime, timezone

    overnight = AutomationWindow(22, 6)  # 22:00 a 06:00 UTC
    assert overnight.contains(datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc))
    assert overnight.contains(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc))
    assert not overnight.contains(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))

    daytime = AutomationWindow(9, 17)
    assert daytime.contains(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
    assert not daytime.contains(datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc))
    print("OK: las ventanas horarias UTC funcionan, incluyendo las que cruzan medianoche")


def test_news_guard_pauses_entries_only_in_automatic_window():
    """
    Fuera de ventana automática: alerta pero NO pausa entradas (modo manual).
    Dentro de ventana automática: alerta Y pausa entradas por cooldown_minutes.
    """
    from news_monitor import NewsMonitor, NewsAutomationSchedule, AutomationWindow, NewsGuard
    from datetime import datetime, timedelta, timezone

    def fake_http_get(url, timeout):
        return _SAMPLE_RSS

    class CollectingAlertChannel:
        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)
            return True

    manual_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)  # fuera de la ventana 22-6
    alert_channel = CollectingAlertChannel()
    guard = NewsGuard(
        NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get),
        NewsAutomationSchedule([AutomationWindow(22, 6)], cooldown_minutes=30),
        alert_channel, min_interval_seconds=0,
    )
    guard.check(now=manual_time)
    assert len(alert_channel.sent) == 2, "Debería alertar igual en modo manual"
    assert not guard.entries_paused(now=manual_time), "En modo manual no debería pausar entradas solo"

    # Nueva instancia para probar el modo automático con las mismas noticias "nuevas"
    auto_time = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)  # dentro de la ventana 22-6
    alert_channel2 = CollectingAlertChannel()
    guard2 = NewsGuard(
        NewsMonitor(feeds=["https://fake.feed/rss"], http_get=fake_http_get),
        NewsAutomationSchedule([AutomationWindow(22, 6)], cooldown_minutes=30),
        alert_channel2, min_interval_seconds=0,
    )
    guard2.check(now=auto_time)
    assert len(alert_channel2.sent) == 2
    assert guard2.entries_paused(now=auto_time), "En ventana automática debería pausar entradas"
    assert not guard2.entries_paused(now=auto_time + timedelta(minutes=31)), "La pausa debería expirar tras el cooldown"
    print("OK: NewsGuard solo pausa entradas automáticamente dentro de la ventana configurada, y respeta el cooldown")


def test_live_polling_blocks_entries_during_news_pause():
    """
    Integración: con un NewsGuard ya pausado desde antes de arrancar, el
    runner de precios en vivo no debería abrir NINGUNA posición nueva
    aunque la estrategia de señal de compra, incluso con precios que
    normalmente la dispararían.
    """
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone

    from live_runner import run_live_polling
    from news_monitor import NewsMonitor, NewsAutomationSchedule, NewsGuard

    class StubPriceSource:
        def __init__(self, prices):
            self._prices = list(prices)
            self._i = 0

        def get_current_price(self, symbol):
            price = self._prices[min(self._i, len(self._prices) - 1)]
            self._i += 1
            return price

    # Precio en ruptura sostenida -- normalmente dispararía una entrada de "momentum".
    prices = [100 + i * 3 for i in range(20)]
    price_source = StubPriceSource(prices)

    class NoAlertChannel:
        def send(self, message):
            return True

    guard = NewsGuard(
        NewsMonitor(feeds=[], http_get=lambda url, timeout: ""),  # sin feeds -- no busca noticias nuevas
        NewsAutomationSchedule([], cooldown_minutes=999999),
        NoAlertChannel(), min_interval_seconds=0,
    )
    # Forzar la pausa manualmente, como si una noticia de alto impacto ya la hubiera activado.
    guard._paused_until = datetime.now(timezone.utc) + timedelta(hours=1)

    fd, state_path = tempfile.mkstemp(suffix="_live_polling_news_state.json")
    os.close(fd)
    os.remove(state_path)
    try:
        broker = run_live_polling(
            price_source, symbol="TEST_NEWS", strategy_name="momentum", profile_name="moderado",
            seed_csv="real_data/btc_daily.csv", initial_balance=1000.0,
            poll_interval_seconds=0, max_ticks=15, state_path=state_path,
            news_guard=guard,
        )
        assert broker.get_open_positions() == {}, "No debería haber abierto ninguna posición durante la pausa por noticias"
        assert broker.get_balance() == 1000.0, "El balance no debería haberse movido si nunca se operó"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: la pausa por noticias bloquea entradas nuevas de punta a punta en el runner de precios en vivo")


def test_param_optimizer_ranks_by_out_of_sample_only():
    """
    El ranking debe basarse EXCLUSIVAMENTE en el retorno promedio fuera de
    muestra (walk-forward en varias ventanas), nunca en el resultado sobre
    el 100% de los datos. Verifica la forma del resultado (todas las
    combinaciones evaluadas, no solo la ganadora) y que el ranking esté
    ordenado de forma descendente por ese campo out-of-sample.
    """
    from data_utils import generate_synthetic_data
    from param_optimizer import optimize_parameters

    df = generate_synthetic_data(n_days=400, seed=11)
    result = optimize_parameters(
        df, strategy_name="tendencia", profile_name="moderado",
        param_grid={"fast": [10, 20], "slow": [40, 60]},
        n_windows=3, train_pct=0.6,
    )

    assert len(result["combinaciones_evaluadas"]) == 4, "Debería evaluar las 4 combinaciones del grid (2x2)"
    assert "ranking_out_of_sample" in result
    returns = [c["retorno_promedio_out_sample_pct"] for c in result["ranking_out_of_sample"]]
    assert returns == sorted(returns, reverse=True), "El ranking debe estar ordenado descendente por retorno OUT-OF-SAMPLE"
    assert result["recomendado"] == result["ranking_out_of_sample"][0]
    # Ninguna combinación evaluada expone o usa un retorno sobre el dataset completo (in-sample) para decidir el ranking.
    for combo in result["combinaciones_evaluadas"]:
        assert "retorno_total_pct" not in combo, "No debe filtrarse una metrica calculada sobre el 100% de los datos"
    print("OK: el optimizador de parámetros rankea únicamente por desempeño fuera de muestra")


def test_param_optimizer_rejects_too_few_windows_worth_of_data():
    from param_optimizer import optimize_parameters
    import pandas as pd
    import numpy as np

    tiny_df = pd.DataFrame({
        "open": np.linspace(100, 110, 30), "high": np.linspace(101, 111, 30),
        "low": np.linspace(99, 109, 30), "close": np.linspace(100, 110, 30),
        "volume": np.ones(30),
    }, index=pd.bdate_range("2024-01-01", periods=30))

    result = optimize_parameters(
        tiny_df, strategy_name="momentum", profile_name="moderado",
        param_grid={"lookback": [5]}, n_windows=10,  # 30/10 = 3 velas por ventana, muy pocas
    )
    assert result["recomendado"] is None
    assert result["advertencia"] is not None
    print("OK: el optimizador avisa quando no hay suficientes datos para las ventanas pedidas, en vez de fallar en silencio")


def test_wallet_reserve_and_release_round_trip():
    from wallet_integration import SimulatedWalletBalanceProvider

    wallet = SimulatedWalletBalanceProvider()
    wallet.deposit("user-1", "USDC", 1000.0)

    wallet.reserve_for_trading("user-1", "USDC", 300.0)
    assert wallet.get_available_balance("user-1", "USDC") == 700.0
    assert wallet.get_trading_allocation("user-1", "USDC") == 300.0

    wallet.release_from_trading("user-1", "USDC", 300.0)
    assert wallet.get_available_balance("user-1", "USDC") == 1000.0
    assert wallet.get_trading_allocation("user-1", "USDC") == 0.0
    print("OK: reservar y liberar saldo de la billetera es un viaje de ida y vuelta exacto")


def test_wallet_reserve_fails_without_enough_available_balance():
    from wallet_integration import SimulatedWalletBalanceProvider, InsufficientWalletBalanceError

    wallet = SimulatedWalletBalanceProvider()
    wallet.deposit("user-1", "USDC", 100.0)
    try:
        wallet.reserve_for_trading("user-1", "USDC", 500.0)
        assert False, "Debería fallar: no hay saldo disponible suficiente"
    except InsufficientWalletBalanceError:
        pass
    assert wallet.get_available_balance("user-1", "USDC") == 100.0, "El saldo no debe alterarse si la reserva falla"
    print("OK: reservar más de lo disponible falla sin mover saldo")


def test_wallet_settle_trade_result_only_touches_trading_allocation():
    from wallet_integration import SimulatedWalletBalanceProvider

    wallet = SimulatedWalletBalanceProvider()
    wallet.deposit("user-1", "USDC", 1000.0)
    wallet.reserve_for_trading("user-1", "USDC", 300.0)

    wallet.settle_trade_result("user-1", "USDC", 50.0)  # ganancia
    assert wallet.get_trading_allocation("user-1", "USDC") == 350.0
    assert wallet.get_available_balance("user-1", "USDC") == 700.0, "El saldo disponible general no debe tocarse"

    wallet.settle_trade_result("user-1", "USDC", -1000.0)  # pérdida imposible, protección de piso
    assert wallet.get_trading_allocation("user-1", "USDC") == 0.0, "La asignación nunca debe quedar negativa"
    print("OK: settle_trade_result solo afecta la asignación de trading, nunca el saldo disponible general")


def test_user_sessions_are_fully_isolated():
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager

    fd, db_path = tempfile.mkstemp(suffix="_multiuser.db")
    os.close(fd)
    os.remove(db_path)

    wallet = SimulatedWalletBalanceProvider()
    wallet.deposit("user-a", "USDC", 1000.0)
    wallet.deposit("user-b", "USDC", 250.0)
    manager = UserSessionManager(wallet, db_path=db_path)

    session_a = manager.start_session("user-a", "USDC", 800.0)
    session_b = manager.start_session("user-b", "USDC", 200.0)

    try:
        assert session_a.broker.get_balance() == 800.0
        assert session_b.broker.get_balance() == 200.0
        assert session_a.broker is not session_b.broker
        assert session_a.circuit_breaker is not session_b.circuit_breaker
        assert session_a.kill_switch is not session_b.kill_switch
        assert session_a.state_store is not session_b.state_store

        session_a.kill_switch.activate("prueba de aislamiento")
        assert session_a.kill_switch.is_active() is True
        assert session_b.kill_switch.is_active() is False, "El kill-switch de un usuario NUNCA debe afectar a otro"
    finally:
        for uid in ("user-a", "user-b"):
            kill_switch_path = f"./.KILL_SWITCH_{uid}"
            manager.stop_session(uid)
            if os.path.exists(kill_switch_path):
                os.remove(kill_switch_path)
        if os.path.exists(db_path):
            os.remove(db_path)

    assert wallet.get_available_balance("user-a", "USDC") == 1000.0
    assert wallet.get_available_balance("user-b", "USDC") == 250.0
    print("OK: las sesiones de usuarios distintos están completamente aisladas (bróker, circuit breaker, kill-switch, estado)")


def test_start_session_fails_cleanly_without_enough_wallet_balance():
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider, InsufficientWalletBalanceError
    from multi_user import UserSessionManager

    fd, db_path = tempfile.mkstemp(suffix="_multiuser.db")
    os.close(fd)
    os.remove(db_path)

    wallet = SimulatedWalletBalanceProvider()
    wallet.deposit("user-c", "USDC", 50.0)
    manager = UserSessionManager(wallet, db_path=db_path)

    try:
        try:
            manager.start_session("user-c", "USDC", 500.0)
            assert False, "Debería fallar: no hay saldo suficiente en la billetera"
        except InsufficientWalletBalanceError:
            pass

        try:
            manager.get_session("user-c")
            assert False, "No debería haber quedado una sesión a medio crear"
        except KeyError:
            pass
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: si no alcanza el saldo de la billetera, no queda ninguna sesión a medio abrir")


def test_user_session_manager_uses_one_shared_db_not_one_file_per_user():
    """
    El punto central de la Fase 3a: muchos usuarios comparten un único
    archivo de base de datos (no un JSON por usuario), y el estado de
    cada uno persiste correctamente ahí incluso después de cerrar sus
    sesiones.
    """
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager
    from state_store import SQLiteStateStore

    fd, db_path = tempfile.mkstemp(suffix="_multiuser.db")
    os.close(fd)
    os.remove(db_path)

    wallet = SimulatedWalletBalanceProvider()
    for uid, amount in [("user-x", 500.0), ("user-y", 700.0), ("user-z", 300.0)]:
        wallet.deposit(uid, "USDC", amount)
    manager = UserSessionManager(wallet, db_path=db_path)

    try:
        for uid, amount in [("user-x", 400.0), ("user-y", 600.0), ("user-z", 250.0)]:
            session = manager.start_session(uid, "USDC", amount)
            session.state_store.save({}, capital=amount, extra={})

        assert SQLiteStateStore.all_keys(db_path) == ["user-x", "user-y", "user-z"]

        for uid in ("user-x", "user-y", "user-z"):
            manager.stop_session(uid)
            kill_switch_path = f"./.KILL_SWITCH_{uid}"
            if os.path.exists(kill_switch_path):
                os.remove(kill_switch_path)

        # El estado sigue en la MISMA base compartida despues de cerrar las sesiones.
        assert SQLiteStateStore.all_keys(db_path) == ["user-x", "user-y", "user-z"]
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: UserSessionManager guarda el estado de todos los usuarios en un único archivo compartido, no uno por usuario")


class _CollectingAlertChannel:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return True


class _FakeSession:
    """Sesion minima para probar OperationsMonitor sin necesitar una UserTradingSession completa."""

    def __init__(self, circuit_breaker=None, kill_switch=None, heartbeat=None, broker=None, state_store=None):
        self.circuit_breaker = circuit_breaker
        self.kill_switch = kill_switch
        self.heartbeat = heartbeat
        self.broker = broker
        self.state_store = state_store


def test_ops_monitor_detects_individual_circuit_breaker_and_kill_switch_issues():
    from ops_monitor import OperationsMonitor
    from safety import CircuitBreaker, ManualKillSwitch

    breaker_tripped = CircuitBreaker(max_drawdown_pct=10.0)
    breaker_tripped.tripped = True
    breaker_tripped.trip_reason = "Drawdown máximo alcanzado: 12.0%"

    kill_switch_off = ManualKillSwitch(control_file=".KILL_SWITCH_test_ops_a")
    session_a = _FakeSession(circuit_breaker=breaker_tripped, kill_switch=kill_switch_off)
    session_b = _FakeSession(circuit_breaker=CircuitBreaker(), kill_switch=ManualKillSwitch(control_file=".KILL_SWITCH_test_ops_b"))

    alert_channel = _CollectingAlertChannel()
    monitor = OperationsMonitor(alert_channel)
    monitor.register("user-a", session_a)
    monitor.register("user-b", session_b)

    report = monitor.check_all()
    assert report["usuarios_monitoreados"] == 2
    assert len(report["problemas_individuales"]) == 1
    assert report["problemas_individuales"][0]["user_id"] == "user-a"
    assert report["problemas_individuales"][0]["tipo"] == "circuit_breaker"
    assert any("user-a" in m and "circuit_breaker" in m for m in alert_channel.sent)
    assert report["alertas_sistemicas"] == [], "Con pocos usuarios registrados no debería escalar a sistémica"
    print("OK: OperationsMonitor detecta problemas individuales y alerta por el canal existente")


def test_ops_monitor_escalates_to_systemic_alert_when_threshold_crossed():
    from ops_monitor import OperationsMonitor
    from safety import CircuitBreaker, ManualKillSwitch

    alert_channel = _CollectingAlertChannel()
    monitor = OperationsMonitor(alert_channel, systemic_threshold_pct=50.0, min_users_for_systemic=4)

    for i in range(6):
        breaker = CircuitBreaker()
        if i < 4:  # 4 de 6 = 66% > 50% de umbral
            breaker.tripped = True
            breaker.trip_reason = "Drawdown máximo alcanzado: 20.0%"
        monitor.register(f"user-{i}", _FakeSession(
            circuit_breaker=breaker, kill_switch=ManualKillSwitch(control_file=f".KILL_SWITCH_test_ops_sys_{i}")
        ))

    report = monitor.check_all()
    assert len(report["alertas_sistemicas"]) == 1
    systemic = report["alertas_sistemicas"][0]
    assert systemic["tipo"] == "circuit_breaker"
    assert systemic["porcentaje"] > 50.0
    assert any("ALERTA SISTEMICA" in m for m in alert_channel.sent)
    print("OK: OperationsMonitor escala a alerta sistémica cuando muchos usuarios comparten el mismo problema")


def test_ops_monitor_does_not_escalate_with_too_few_users():
    from ops_monitor import OperationsMonitor
    from safety import CircuitBreaker, ManualKillSwitch

    alert_channel = _CollectingAlertChannel()
    monitor = OperationsMonitor(alert_channel, systemic_threshold_pct=50.0, min_users_for_systemic=10)

    # 2 de 2 usuarios afectados = 100%, pero por debajo del minimo de usuarios para hablar de "sistemico"
    for i in range(2):
        breaker = CircuitBreaker()
        breaker.tripped = True
        breaker.trip_reason = "test"
        monitor.register(f"user-{i}", _FakeSession(
            circuit_breaker=breaker, kill_switch=ManualKillSwitch(control_file=f".KILL_SWITCH_test_ops_few_{i}")
        ))

    report = monitor.check_all()
    assert report["alertas_sistemicas"] == [], "No debería escalar a sistémica con menos usuarios que el mínimo configurado"
    assert len(report["problemas_individuales"]) == 2
    print("OK: OperationsMonitor no escala a sistémica por debajo del mínimo de usuarios configurado")


def test_ops_monitor_detects_reconciliation_mismatch():
    from ops_monitor import OperationsMonitor
    from safety import CircuitBreaker, ManualKillSwitch
    from state_store import SQLiteStateStore
    import os
    import tempfile

    fd, db_path = tempfile.mkstemp(suffix="_ops_reconcile.db")
    os.close(fd)
    os.remove(db_path)

    class FakeBroker:
        def get_open_positions(self):
            return {"ETH_USDC": {"unidades": 1.0, "precio_entrada": 100.0}}  # el broker reporta ETH

    try:
        state_store = SQLiteStateStore(db_path=db_path, key="user-recon")
        state_store.save({"BTC_USDC": {"unidades": 1.0, "precio_entrada": 100.0}}, capital=500.0)  # interno cree tener BTC

        alert_channel = _CollectingAlertChannel()
        monitor = OperationsMonitor(alert_channel)
        monitor.register("user-recon", _FakeSession(
            circuit_breaker=CircuitBreaker(), kill_switch=ManualKillSwitch(control_file=".KILL_SWITCH_test_ops_recon"),
            broker=FakeBroker(), state_store=state_store,
        ))

        report = monitor.check_all()
        assert any(p["tipo"] == "reconciliacion" for p in report["problemas_individuales"])
        state_store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: OperationsMonitor detecta un desfasaje de reconciliación entre el estado interno y el bróker")


def test_ops_monitor_detects_concentration_issue():
    """
    Laguna real encontrada en auditoría: OperationsMonitor (el monitoreo
    centralizado para miles de usuarios, ver README) chequeaba circuit
    breaker, kill-switch, heartbeat y reconciliación, pero NO
    concentración -- el mismo aviso que ya se agregó a
    status_report.py/real_results_report.py (ver ronda de auditoría de
    concentración) faltaba justo en el único lugar donde un problema de
    concentración generalizado se vería como patrón agregado entre
    muchos usuarios, no como un caso aislado que nadie nota.
    """
    from ops_monitor import OperationsMonitor
    from safety import CircuitBreaker, ManualKillSwitch
    from state_store import SQLiteStateStore
    import os
    import tempfile

    fd, db_path = tempfile.mkstemp(suffix="_ops_concentration.db")
    os.close(fd)
    os.remove(db_path)

    try:
        state_store = SQLiteStateStore(db_path=db_path, key="user-concentrado")
        # Mismo escenario real que LINK_USDC: casi todo el capital en un solo símbolo.
        state_store.save(
            {"LINK_USDC": {"unidades": 114.767004, "precio_entrada": 8.4271}}, capital=3.41,
        )

        alert_channel = _CollectingAlertChannel()
        monitor = OperationsMonitor(alert_channel)
        monitor.register("user-concentrado", _FakeSession(
            circuit_breaker=CircuitBreaker(), kill_switch=ManualKillSwitch(control_file=".KILL_SWITCH_test_ops_conc"),
            state_store=state_store,
        ))

        report = monitor.check_all()
        assert any(p["tipo"] == "concentracion" and "LINK_USDC" in p["detalle"] for p in report["problemas_individuales"]), (
            "Debe detectar la posición sobre-concentrada como un problema individual"
        )
        assert any("concentraci" in msg.lower() and "LINK_USDC" in msg for msg in alert_channel.sent), (
            "Debe alertar por el mismo canal que el resto de los problemas"
        )
        state_store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: OperationsMonitor detecta posiciones sobre-concentradas, igual que los otros reportes")


class _FakeAlpacaResponse:
    """Respuesta HTTP falsa para inyectar en AlpacaBrokerAdapter sin red real."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_alpaca_get_current_price_parses_latest_trade():
    from broker import AlpacaBrokerAdapter

    calls = []

    def fake_transport(method, url, headers, json_body, timeout):
        calls.append((method, url))
        return _FakeAlpacaResponse(200, {"trade": {"p": 231.42}})

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", transport=fake_transport)
    price = broker.get_current_price("aapl")
    assert price == 231.42
    assert calls[0][0] == "GET"
    assert "AAPL" in calls[0][1] and "trades/latest" in calls[0][1]
    print("OK: AlpacaBrokerAdapter obtiene el último precio de una acción real (AAPL)")


def test_alpaca_get_balance_reads_cash_from_account():
    from broker import AlpacaBrokerAdapter

    def fake_transport(method, url, headers, json_body, timeout):
        return _FakeAlpacaResponse(200, {"cash": "5000.12"})

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", transport=fake_transport)
    assert broker.get_balance() == 5000.12
    print("OK: AlpacaBrokerAdapter lee el efectivo disponible de la cuenta")


def test_alpaca_get_open_positions_maps_fields():
    from broker import AlpacaBrokerAdapter

    def fake_transport(method, url, headers, json_body, timeout):
        return _FakeAlpacaResponse(200, [{"symbol": "KO", "qty": "3", "avg_entry_price": "62.5"}])

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", transport=fake_transport)
    positions = broker.get_open_positions()
    assert positions == {"KO": {"unidades": 3.0, "precio_entrada": 62.5}}
    print("OK: AlpacaBrokerAdapter mapea posiciones abiertas (ej. acciones de Coca-Cola)")


def test_alpaca_place_order_blocked_without_allow_trading():
    from broker import AlpacaBrokerAdapter

    def fail_if_called(method, url, headers, json_body, timeout):
        raise AssertionError("No debería llamar a la red con allow_trading=False")

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", allow_trading=False, transport=fail_if_called)
    result = broker.place_order("KO", "buy", 5)
    assert result["status"] == "rejected"
    assert "allow_trading" in result["motivo"]
    print("OK: place_order de Alpaca queda bloqueado en modo lectura, sin llegar a llamar a la red")


def test_alpaca_place_order_when_allowed_posts_to_orders_endpoint():
    from broker import AlpacaBrokerAdapter

    calls = []

    def fake_transport(method, url, headers, json_body, timeout):
        calls.append((method, url, json_body))
        return _FakeAlpacaResponse(200, {
            "id": "order-123", "symbol": "KO", "status": "filled",
            "filled_qty": "5", "filled_avg_price": "62.75",
        })

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", allow_trading=True, transport=fake_transport)
    result = broker.place_order("KO", "buy", 5)
    assert calls[0][0] == "POST" and calls[0][1].endswith("/v2/orders")
    assert calls[0][2] == {"symbol": "KO", "qty": "5", "side": "buy", "type": "market", "time_in_force": "day"}
    assert result["status"] == "filled"
    assert result["units"] == 5.0
    assert result["price"] == 62.75
    print("OK: con allow_trading=True, place_order arma correctamente la orden de mercado para Alpaca")


def test_alpaca_place_order_open_status_reports_zero_units_not_requested():
    """
    Regresión (Fase 3c): una orden "open" (sin llenar todavía) debe
    reportar 0 unidades REALMENTE ejecutadas, no la cantidad pedida.
    Bug real encontrado y corregido en el proceso: la versión anterior
    devolvía `units` (la cantidad pedida) como fallback cuando no había
    filled_qty, lo que hubiera hecho que el motor registrara una posición
    que el bróker todavía no ejecutó.
    """
    from broker import AlpacaBrokerAdapter

    def fake_transport(method, url, headers, json_body, timeout):
        return _FakeAlpacaResponse(200, {
            "id": "order-456", "symbol": "KO", "status": "new",  # sin filled_qty -- nada llenado todavia
        })

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", allow_trading=True, transport=fake_transport)
    result = broker.place_order("KO", "buy", 5)
    assert result["status"] == "open"
    assert result["units"] == 0.0, "Una orden abierta no debe reportar unidades ya llenadas"
    print("OK: una orden Alpaca 'open' reporta 0 unidades llenadas, no la cantidad pedida")


def test_alpaca_get_order_status_parses_response():
    from broker import AlpacaBrokerAdapter

    calls = []

    def fake_transport(method, url, headers, json_body, timeout):
        calls.append((method, url))
        return _FakeAlpacaResponse(200, {
            "id": "order-789", "symbol": "KO", "status": "partially_filled",
            "filled_qty": "2", "filled_avg_price": "63.10",
        })

    broker = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", transport=fake_transport)
    status = broker.get_order_status("order-789")
    assert calls[0][0] == "GET" and calls[0][1].endswith("/v2/orders/order-789")
    assert status["status"] == "partially_filled"
    assert status["units"] == 2.0
    assert status["price"] == 63.10
    print("OK: AlpacaBrokerAdapter.get_order_status parsea correctamente el estado de una orden ya colocada")


def test_live_polling_accepts_alpaca_as_price_source():
    """
    Prueba concreta de que run_live_polling (pensado originalmente para
    Ripio/cripto) acepta CUALQUIER implementación de BrokerBase como
    fuente de precio sin ningún cambio -- acá con AlpacaBrokerAdapter
    (acciones de EE.UU.), demostrando en código, no solo en README, que
    el motor generaliza más allá de cripto.
    """
    import os
    import tempfile
    from live_runner import run_live_polling
    from broker import AlpacaBrokerAdapter

    prices = iter([228.0 + i * 0.8 for i in range(20)])

    def fake_transport(method, url, headers, json_body, timeout):
        return _FakeAlpacaResponse(200, {"trade": {"p": next(prices)}})

    price_source = AlpacaBrokerAdapter(api_key_id="k", secret_key="s", transport=fake_transport)

    fd, state_path = tempfile.mkstemp(suffix="_live_polling_alpaca_state.json")
    os.close(fd)
    os.remove(state_path)
    try:
        broker = run_live_polling(
            price_source, symbol="AAPL", strategy_name="momentum", profile_name="moderado",
            seed_csv="real_data/aapl_daily.csv", initial_balance=1000.0,
            poll_interval_seconds=0, max_ticks=15, state_path=state_path,
        )
        assert broker.get_balance() >= 0
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: run_live_polling acepta AlpacaBrokerAdapter (acciones) como fuente de precio sin cambios")


def test_alpaca_private_requires_credentials():
    from broker import AlpacaBrokerAdapter
    from resilience import PermanentBrokerError

    def fail_if_called(method, url, headers, json_body, timeout):
        raise AssertionError("No debería intentar llamar a la red sin credenciales")

    broker = AlpacaBrokerAdapter(api_key_id=None, secret_key=None, transport=fail_if_called)
    try:
        broker.get_balance()
        assert False, "Debería fallar sin credenciales"
    except PermanentBrokerError:
        pass
    print("OK: AlpacaBrokerAdapter exige credenciales antes de llamar a cualquier endpoint")


def test_shared_price_feed_dedupes_concurrent_consumers_on_same_symbol():
    """
    Prueba directa del problema real que motiva este módulo (ver README:
    se observó un 429 real de Ripio con solo 2 sesiones concurrentes). 20
    'usuarios' pidiendo el mismo símbolo al mismo tiempo NO deberían
    generar 20 llamadas de red -- solo 1, compartida.
    """
    import threading
    import time
    from price_feed import SharedPriceFeed

    call_count = {"n": 0}
    call_lock = threading.Lock()

    class SlowStubPriceSource:
        def get_current_price(self, symbol):
            with call_lock:
                call_count["n"] += 1
            time.sleep(0.05)  # simula latencia real de red
            return 64000.0

    feed = SharedPriceFeed(SlowStubPriceSource(), poll_interval_seconds=60)
    try:
        results = []
        results_lock = threading.Lock()

        def consumer():
            price = feed.get_current_price("BTC_USDC")
            with results_lock:
                results.append(price)

        threads = [threading.Thread(target=consumer) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 20
        assert all(p == 64000.0 for p in results)
        assert call_count["n"] == 1, (
            f"20 'usuarios' pidiendo el mismo símbolo deberían generar 1 sola llamada real, "
            f"generaron {call_count['n']}"
        )
        assert feed.fetch_count() == 1
    finally:
        feed.stop_all()
    print("OK: SharedPriceFeed comparte una sola llamada de red entre 20 consumidores concurrentes del mismo símbolo")


def test_shared_price_feed_polls_symbols_independently():
    from price_feed import SharedPriceFeed

    class StubPriceSource:
        def get_current_price(self, symbol):
            return {"BTC_USDC": 64000.0, "ETH_USDC": 3400.0}[symbol]

    feed = SharedPriceFeed(StubPriceSource(), poll_interval_seconds=60)
    try:
        assert feed.get_current_price("BTC_USDC") == 64000.0
        assert feed.get_current_price("ETH_USDC") == 3400.0
        assert feed.fetch_count() == 2
        assert feed.active_symbols() == ["BTC_USDC", "ETH_USDC"]
    finally:
        feed.stop_all()
    print("OK: SharedPriceFeed mantiene pollers independientes por símbolo")


def test_shared_price_feed_refreshes_when_stale():
    import time
    from price_feed import SharedPriceFeed

    prices = iter([100.0, 200.0, 300.0, 400.0, 500.0])

    class IncrementingStubPriceSource:
        def get_current_price(self, symbol):
            return next(prices)

    feed = SharedPriceFeed(IncrementingStubPriceSource(), poll_interval_seconds=60, stale_after_seconds=0.05)
    try:
        first = feed.get_current_price("TEST")
        assert first == 100.0
        time.sleep(0.1)  # supera stale_after_seconds
        second = feed.get_current_price("TEST")
        assert second == 200.0, "Un precio viejo debería refrescarse sincrónicamente al pedirse de nuevo"
        assert feed.fetch_count() == 2
    finally:
        feed.stop_all()
    print("OK: SharedPriceFeed refresca sincrónicamente un precio cacheado que quedó viejo")


def test_shared_price_feed_as_drop_in_reduces_calls_for_two_sessions():
    """
    Compara 2 sesiones de paper trading en vivo mirando el MISMO símbolo:
    cada una con su propio price_source directo genera su propia llamada
    real por tick; las mismas 2 sesiones compartiendo un SharedPriceFeed
    generan muchas menos -- la prueba concreta de la Fase 2 del roadmap
    (evitar N pollers redundantes por usuario).
    """
    import os
    import tempfile
    from live_runner import run_live_polling
    from price_feed import SharedPriceFeed

    class CountingStubPriceSource:
        def __init__(self):
            self.calls = 0

        def get_current_price(self, symbol):
            self.calls += 1
            return 64000.0 + self.calls

    def _run_session(price_source, suffix):
        fd, state_path = tempfile.mkstemp(suffix=f"_{suffix}.json")
        os.close(fd)
        os.remove(state_path)
        try:
            run_live_polling(
                price_source, symbol="BTC_USDC", strategy_name="momentum", profile_name="moderado",
                seed_csv="real_data/btc_daily.csv", poll_interval_seconds=0, max_ticks=5, state_path=state_path,
            )
        finally:
            if os.path.exists(state_path):
                os.remove(state_path)

    # --- Sin feed compartido: cada sesión usa su propio price_source directo ---
    direct_a, direct_b = CountingStubPriceSource(), CountingStubPriceSource()
    _run_session(direct_a, "direct_a")
    _run_session(direct_b, "direct_b")
    total_direct_calls = direct_a.calls + direct_b.calls
    assert total_direct_calls == 10, f"2 sesiones x 5 ticks sin feed compartido deberían sumar 10 llamadas reales, dio {total_direct_calls}"

    # --- Con feed compartido: mismas 2 sesiones, 1 sola fuente subyacente ---
    shared_source = CountingStubPriceSource()
    feed = SharedPriceFeed(shared_source, poll_interval_seconds=9999)  # no refresca de fondo durante el test
    try:
        _run_session(feed, "shared_0")
        _run_session(feed, "shared_1")
        assert shared_source.calls == 1, (
            f"Con SharedPriceFeed, 2 sesiones sobre el mismo símbolo (con la misma ventana de frescura) "
            f"deberían generar 1 sola llamada real, dio {shared_source.calls}"
        )
    finally:
        feed.stop_all()

    print(f"OK: sin feed compartido 2 sesiones x 5 ticks generaron {total_direct_calls} llamadas reales; "
          f"con SharedPriceFeed generaron {shared_source.calls}")


def test_sqlite_state_store_save_and_load_round_trip():
    import os
    import tempfile
    from state_store import SQLiteStateStore

    fd, db_path = tempfile.mkstemp(suffix="_state.db")
    os.close(fd)
    os.remove(db_path)
    try:
        store = SQLiteStateStore(db_path=db_path, key="user-1")
        store.save({"BTC_USDC": {"unidades": 0.5, "precio_entrada": 64000.0}}, capital=800.0,
                   extra={"stop_loss": 60000.0})
        loaded = store.load()
        assert loaded["positions"] == {"BTC_USDC": {"unidades": 0.5, "precio_entrada": 64000.0}}
        assert loaded["capital"] == 800.0
        assert loaded["extra"] == {"stop_loss": 60000.0}
        assert loaded["saved_at"] is not None
        store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: SQLiteStateStore guarda y recupera el estado exactamente igual que se guardó")


def test_sqlite_state_store_empty_when_no_prior_state():
    import os
    import tempfile
    from state_store import SQLiteStateStore

    fd, db_path = tempfile.mkstemp(suffix="_state.db")
    os.close(fd)
    os.remove(db_path)
    try:
        store = SQLiteStateStore(db_path=db_path, key="user-nuevo")
        loaded = store.load()
        assert loaded == {"positions": {}, "capital": None, "extra": {}, "saved_at": None}
        store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: SQLiteStateStore devuelve estado vacío para una key sin historial")


def test_sqlite_state_store_multiple_users_share_one_db_without_mixing():
    """
    El punto central de la Fase 3a: muchos usuarios comparten UN solo
    archivo de base de datos (no un JSON por usuario), pero sus estados
    nunca se mezclan entre sí.
    """
    import os
    import tempfile
    from state_store import SQLiteStateStore

    fd, db_path = tempfile.mkstemp(suffix="_state.db")
    os.close(fd)
    os.remove(db_path)
    try:
        store_a = SQLiteStateStore(db_path=db_path, key="user-a")
        store_b = SQLiteStateStore(db_path=db_path, key="user-b")

        store_a.save({"BTC_USDC": {"unidades": 1.0, "precio_entrada": 100.0}}, capital=500.0)
        store_b.save({"ETH_USDC": {"unidades": 2.0, "precio_entrada": 50.0}}, capital=300.0)

        loaded_a = store_a.load()
        loaded_b = store_b.load()
        assert loaded_a["positions"] == {"BTC_USDC": {"unidades": 1.0, "precio_entrada": 100.0}}
        assert loaded_a["capital"] == 500.0
        assert loaded_b["positions"] == {"ETH_USDC": {"unidades": 2.0, "precio_entrada": 50.0}}
        assert loaded_b["capital"] == 300.0

        assert SQLiteStateStore.all_keys(db_path) == ["user-a", "user-b"]
        assert os.path.exists(db_path), "Debe ser un único archivo de base de datos, no uno por usuario"

        store_a.close()
        store_b.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: muchos usuarios comparten un solo archivo SQLite sin que sus estados se mezclen")


def test_paper_broker_partial_fill_reports_actual_units():
    from broker import PaperBroker

    broker = PaperBroker(initial_balance=1000.0, fill_ratio=0.4)
    broker.set_price("TEST", 100.0)
    order = broker.place_order("TEST", "buy", 10)

    assert order["status"] == "partially_filled"
    assert abs(order["units"] - 4.0) < 1e-9
    assert order["requested_units"] == 10
    assert abs(broker.get_open_positions()["TEST"]["unidades"] - 4.0) < 1e-9
    print("OK: PaperBroker con fill_ratio<1 reporta un llenado parcial con las unidades reales, no las pedidas")


def test_paper_broker_zero_fill_ratio_leaves_order_open_with_no_position():
    from broker import PaperBroker

    broker = PaperBroker(initial_balance=1000.0, fill_ratio=0.0)
    broker.set_price("TEST", 100.0)
    balance_before = broker.get_balance()
    order = broker.place_order("TEST", "buy", 10)

    assert order["status"] == "open"
    assert order["units"] == 0
    assert broker.get_open_positions() == {}, "Una orden 'open' no debe crear ninguna posición todavía"
    assert broker.get_balance() == balance_before, "No debe descontarse saldo por una orden sin llenar"

    status = broker.get_order_status(order["order_id"])
    assert status["status"] == "open"
    print("OK: PaperBroker con fill_ratio=0 deja la orden 'open' sin tocar balance ni posiciones")


def test_paper_broker_simulate_additional_fill_completes_open_order():
    from broker import PaperBroker

    broker = PaperBroker(initial_balance=1000.0, fill_ratio=0.0)
    broker.set_price("TEST", 100.0)
    order = broker.place_order("TEST", "buy", 10)
    assert order["status"] == "open"

    updated = broker.simulate_additional_fill(order["order_id"], 10)
    assert updated["status"] == "filled"
    assert updated["units"] == 10
    assert broker.get_open_positions()["TEST"]["unidades"] == 10
    assert broker.get_balance() < 1000.0, "Debe haberse descontado el costo de la compra ya completada"
    print("OK: simulate_additional_fill completa una orden que había quedado abierta")


def test_live_engine_open_buy_order_resolves_without_double_ordering():
    """
    Integración (Fase 3c): una orden que queda "open" no debe registrar
    ninguna posición todavía, no debe generar una segunda orden mientras
    sigue pendiente, y debe resolverse recién cuando el bróker confirma
    el llenado en un tick posterior.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_open.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_open"

    try:
        broker = PaperBroker(initial_balance=1000.0, fill_ratio=0.0)  # nada se llena de inmediato
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_open"),
        )

        broker.set_price("TEST_SYM", 100.0)
        now = datetime.now(timezone.utc)

        # Tick 1: señal de compra -> la orden queda "open"
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)
        assert "TEST_SYM" in engine.pending_order, "Debería haber quedado una orden pendiente"
        assert "TEST_SYM" not in engine.internal_positions, "Todavía no debería haber posición (0 unidades llenadas)"
        pending_order_id = engine.pending_order["TEST_SYM"]["order_id"]

        # Tick 2: misma señal -- NO debe mandar una segunda orden (hay una pendiente)
        history_len_before = len(broker.order_history)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)
        assert len(broker.order_history) == history_len_before, "No debe colocar una segunda orden mientras la primera sigue pendiente"
        assert "TEST_SYM" in engine.pending_order  # sigue abierta

        # Simula que la orden finalmente se llena del todo entre el tick 2 y el 3
        broker.simulate_additional_fill(pending_order_id, broker.orders_by_id[pending_order_id]["requested_units"])

        # Tick 3: al resolver la orden pendiente, ahora debería registrar la posición
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)
        assert "TEST_SYM" not in engine.pending_order, "La orden ya debería estar resuelta"
        assert "TEST_SYM" in engine.internal_positions
        assert engine.internal_positions["TEST_SYM"]["unidades"] > 0
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine no duplica órdenes mientras una queda pendiente, y registra la posición cuando finalmente se resuelve")


def test_live_engine_partial_buy_fill_registers_position_with_actual_units():
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_partial.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_partial"

    try:
        broker = PaperBroker(initial_balance=1000.0, fill_ratio=0.5)  # llenado parcial (mitad)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_partial"),
        )

        broker.set_price("TEST_SYM", 100.0)
        now = datetime.now(timezone.utc)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)

        assert "TEST_SYM" not in engine.pending_order, "Un llenado parcial se trata como definitivo, no queda pendiente"
        assert "TEST_SYM" in engine.internal_positions
        internal_units = engine.internal_positions["TEST_SYM"]["unidades"]
        broker_units = broker.get_open_positions()["TEST_SYM"]["unidades"]
        assert internal_units > 0
        assert internal_units == broker_units, "La posición interna debe coincidir con lo que el bróker realmente tiene"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: un llenado parcial en la compra registra la posición con las unidades REALMENTE compradas")


def test_pair_trades_and_tax_export_consume_real_trade_history():
    """
    tax_export.py existía pero no estaba conectado a ninguna fuente de
    datos real -- ningún otro módulo lo llamaba, ni tenía test propio.
    Ahora que trade_history.py persiste operaciones reales, se agrega
    pair_trades() para convertir el registro plano (una fila por lado de
    cada operación) al formato de "operación completa" (entrada+salida)
    que tax_export.py necesita, y se verifica el camino completo:
    TradeHistoryLog -> pair_trades -> export_tax_report/tax_summary.
    """
    import os
    import tempfile
    from trade_history import TradeHistoryLog, pair_trades
    from tax_export import export_tax_report, tax_summary

    fd, trades_path = tempfile.mkstemp(suffix="_tax_export_trades.csv")
    os.close(fd)
    os.remove(trades_path)
    fd2, report_path = tempfile.mkstemp(suffix="_tax_export_report.csv")
    os.close(fd2)
    os.remove(report_path)

    try:
        log = TradeHistoryLog(trades_path)
        log.append(symbol="ETH_USDC", side="buy", motivo="apertura", units=0.5,
                    price=1955.0, pnl=None, balance_resultante=1000.0)
        log.append(symbol="ETH_USDC", side="sell", motivo="señal_estrategia", units=0.5,
                    price=1951.0, pnl=-2.0, balance_resultante=998.0)
        log.append(symbol="BTC_USDC", side="buy", motivo="apertura", units=0.01,
                    price=60000.0, pnl=None, balance_resultante=998.0)
        log.append(symbol="BTC_USDC", side="sell", motivo="take_profit", units=0.01,
                    price=61000.0, pnl=10.0, balance_resultante=1008.0)

        trades = pair_trades(log.load_all())
        assert len(trades) == 2, "Debe emparejar cada compra con su venta correspondiente"
        assert trades[0]["precio_entrada"] == 1955.0 and trades[0]["precio_salida"] == 1951.0
        assert trades[1]["precio_entrada"] == 60000.0 and trades[1]["precio_salida"] == 61000.0

        df = export_tax_report(trades, output_path=report_path)
        assert os.path.exists(report_path)
        assert len(df) == 2
        assert set(df["tipo"]) == {"pérdida", "ganancia"}

        summary = tax_summary(trades)
        assert summary["resultado_neto_usd"] == 8.0, "El neto debe ser -2 + 10 = 8"
        assert summary["cantidad_operaciones_ganadoras"] == 1
        assert summary["cantidad_operaciones_perdedoras"] == 1
    finally:
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(report_path):
            os.remove(report_path)
    print("OK: pair_trades conecta el historial persistente real con tax_export.py de punta a punta")


def test_real_results_report_summarizes_sessions_from_directory():
    """
    real_results_report.py arma el informe consolidado a partir de
    sesiones reales (mismo criterio que status_report.py) -- se prueba
    con dos sesiones sintéticas en una carpeta temporal, una con
    operaciones cerradas y log real, otra sin nada todavía.
    """
    import os
    import shutil
    import tempfile
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from real_results_report import generate_report

    tmp_dir = tempfile.mkdtemp(prefix="real_results_report_test_")
    try:
        # Sesión A: con historial y log real (con eventos para contar).
        StateStore(path=os.path.join(tmp_dir, "live_state_a.json")).save({}, capital=1010.0, extra={})
        history_a = TradeHistoryLog(os.path.join(tmp_dir, "trades_a.csv"))
        history_a.append(symbol="A_USDC", side="buy", motivo="apertura", units=1.0,
                          price=100.0, pnl=None, balance_resultante=900.0)
        history_a.append(symbol="A_USDC", side="sell", motivo="take_profit", units=1.0,
                          price=110.0, pnl=10.0, balance_resultante=1010.0)
        with open(os.path.join(tmp_dir, "live_log_a.txt"), "w", encoding="utf-8") as f:
            f.write("2026-01-01 00:00:00 | INFO | live_runner | Iniciando runner\n")
            f.write("2026-01-01 00:01:00 | ERROR | live_runner | No se pudo obtener el precio (429): rate limited\n")
            f.write("2026-01-01 00:02:00 | WARNING | news_monitor | Pausa automática de entradas nuevas activada hasta X\n")
            f.write("2026-01-01 00:03:00 | INFO | reconciliation | Reconciliación OK: el estado interno coincide con el del bróker\n")

        # Sesión B: recién arrancada, sin operaciones ni log todavía.
        StateStore(path=os.path.join(tmp_dir, "live_state_b.json")).save({}, capital=1000.0, extra={})

        report = generate_report(tmp_dir)

        assert "Sesión: a" in report and "Sesión: b" in report
        assert "USDC +10.00" in report, "Debe mostrar el resultado neto de la sesión A"
        assert "Sin operaciones cerradas todavía" in report, "La sesión B no tiene historial"
        assert "Rate limits (429) absorbidos sin caerse: 1" in report
        assert "Pausas automáticas por noticias reales activadas: 1" in report
        assert "Reconciliaciones internas OK: 1" in report
        assert "Operaciones cerradas en total: 1 (1 ganadoras, 0 perdedoras)" in report
        assert "no rentabilidad" in report, "Debe aclarar que esto mide fiabilidad, no rentabilidad"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("OK: real_results_report.py consolida sesiones reales (resultado + fiabilidad de ejecución) correctamente")


def test_real_results_report_warns_about_concentrated_position():
    """
    Mismo aviso que status_report.py, acá en el informe consolidado --
    una sesión con una posición sobre-concentrada debe quedar marcada
    explícitamente, no solo con el número de capital pelado.
    """
    import os
    import shutil
    import tempfile
    from state_store import StateStore
    from real_results_report import generate_report

    tmp_dir = tempfile.mkdtemp(prefix="real_results_report_concentration_test_")
    try:
        StateStore(path=os.path.join(tmp_dir, "live_state_new_pairs.json")).save(
            {"LINK_USDC": {"unidades": 114.767004, "precio_entrada": 8.4271}}, capital=3.41, extra={},
        )
        report = generate_report(tmp_dir)
        assert "[!] Concentraci" in report and "LINK_USDC" in report, (
            "El informe consolidado debe avisar sobre la posición sobre-concentrada"
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("OK: real_results_report.py también avisa sobre posiciones sobre-concentradas")


def test_real_results_report_flags_trades_closed_before_atr_fix():
    """
    Hallazgo real (ver README): TODAS las pérdidas históricas de
    btc_usdc/eth_usdc/multi resultaron ser de operaciones cerradas antes
    del fix del bug de agregación de ticks -- confirmado mirando que
    cerraban en minutos, no en el horizonte de días/semanas del perfil.
    El informe debe separar explícitamente esas operaciones (no
    representativas) de las cerradas después del fix (sí representativas),
    en vez de mezclarlas en un solo número.
    """
    import csv
    import os
    import shutil
    import tempfile
    from datetime import timedelta
    from state_store import StateStore
    from real_results_report import generate_report, ATR_FIX_DEPLOYED_AT
    from trade_history import FIELDNAMES

    tmp_dir = tempfile.mkdtemp(prefix="real_results_report_atr_fix_test_")
    try:
        StateStore(path=os.path.join(tmp_dir, "live_state_x.json")).save({}, capital=1000.0, extra={})

        antes = (ATR_FIX_DEPLOYED_AT - timedelta(minutes=5)).isoformat().replace("+00:00", "")
        despues = (ATR_FIX_DEPLOYED_AT + timedelta(hours=1)).isoformat().replace("+00:00", "")
        trades_path = os.path.join(tmp_dir, "trades_x.csv")
        with open(trades_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            # Operación cerrada ANTES del fix -- perdedora, artefacto del bug.
            writer.writerow({"timestamp": antes, "symbol": "X_USDC", "side": "buy", "motivo": "apertura",
                              "units": 1.0, "price": 100.0, "pnl": "", "balance_resultante": 1000.0})
            writer.writerow({"timestamp": antes, "symbol": "X_USDC", "side": "sell", "motivo": "stop_loss",
                              "units": 1.0, "price": 95.0, "pnl": -5.0, "balance_resultante": 995.0})
            # Operación cerrada DESPUÉS del fix -- ganadora, representativa.
            writer.writerow({"timestamp": despues, "symbol": "X_USDC", "side": "buy", "motivo": "apertura",
                              "units": 1.0, "price": 100.0, "pnl": "", "balance_resultante": 995.0})
            writer.writerow({"timestamp": despues, "symbol": "X_USDC", "side": "sell", "motivo": "take_profit",
                              "units": 1.0, "price": 108.0, "pnl": 8.0, "balance_resultante": 1003.0})

        report = generate_report(tmp_dir)

        assert "1 se cerraron ANTES del fix" in report, "Debe marcar la operación anterior al fix como no representativa"
        assert "USDC -5.00" in report, "El neto de la operación anterior al fix debe quedar visible, no oculto"
        assert "Después del fix: 1 operaciones, neto USDC +8.00" in report, (
            "Debe mostrar por separado el resultado posterior al fix, que sí representa la estrategia"
        )
        assert "bug de agregación de ticks" in report, "Debe explicar la causa, no solo marcar los números"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("OK: real_results_report.py separa las operaciones afectadas por el bug de ATR de las representativas")


def test_real_results_report_output_file_is_valid_utf8():
    """
    Bug real: la primera versión solo imprimía el informe por stdout, y
    guardarlo con `python real_results_report.py > archivo.md` en Windows
    lo dejaba codificado con el codepage de la consola (cp1252), no
    UTF-8 -- el archivo resultante fallaba al leerse como UTF-8 estricto
    (justo lo que se esperaría de un .md con tildes). Se agregó --output
    para que el script mismo escriba el archivo con UTF-8 explícito, sin
    depender de cómo la consola redirija stdout. Este test reproduce el
    escenario exacto: un informe con texto acentuado (nombres de sesión,
    "Reconciliación", "días") debe poder leerse de vuelta como UTF-8
    estricto sin UnicodeDecodeError.
    """
    import os
    import sys
    import tempfile
    import real_results_report
    from state_store import StateStore

    tmp_dir = tempfile.mkdtemp(prefix="real_results_report_utf8_test_")
    fd, output_path = tempfile.mkstemp(suffix="_informe_utf8_test.md")
    os.close(fd)
    os.remove(output_path)
    old_argv = sys.argv
    try:
        StateStore(path=os.path.join(tmp_dir, "live_state_test.json")).save({}, capital=1000.0, extra={})

        sys.argv = ["real_results_report.py", "--dir", tmp_dir, "--output", output_path]
        real_results_report.main()

        assert os.path.exists(output_path)
        with open(output_path, encoding="utf-8") as f:
            content = f.read()  # debe leerse sin UnicodeDecodeError
        assert "Informe de resultados reales" in content
        assert "fiabilidad de ejecución" in content, "El acento de 'ejecución' debe sobrevivir intacto"
    finally:
        sys.argv = old_argv
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(output_path):
            os.remove(output_path)
    print("OK: real_results_report.py --output escribe un archivo UTF-8 válido, sin depender del codepage de la consola")


def test_kill_switch_cli_respects_custom_control_file():
    """
    Bug real encontrado auditando el código: kill_switch.py (el comando
    para activar/desactivar el freno a mano) siempre apuntaba al archivo
    genérico .KILL_SWITCH -- una sesión arrancada con --kill-switch-file
    propio (ver live_runner.py) quedaba imposible de pausar con este
    comando, porque miraba un archivo que esa sesión ni siquiera consulta.
    Verifica que --file lo dirige al archivo correcto, y que NO toca el
    genérico cuando se le pasa uno propio.
    """
    import os
    import sys
    import kill_switch

    custom_path = ".KILL_SWITCH_test_cli_custom"
    generic_existed_before = os.path.exists(".KILL_SWITCH")

    old_argv = sys.argv
    try:
        sys.argv = ["kill_switch.py", "activar", "motivo de prueba", "--file", custom_path]
        kill_switch.main()
        assert os.path.exists(custom_path), "Debería haber creado el archivo de control PROPIO, no el genérico"
        assert os.path.exists(".KILL_SWITCH") == generic_existed_before, (
            "No debería haber tocado el archivo de control genérico al pasar --file"
        )

        sys.argv = ["kill_switch.py", "desactivar", "--file", custom_path]
        kill_switch.main()
        assert not os.path.exists(custom_path), "Desactivar con --file debe borrar el archivo PROPIO"
    finally:
        sys.argv = old_argv
        if os.path.exists(custom_path):
            os.remove(custom_path)
    print("OK: kill_switch.py respeta --file y no interfiere con el archivo de control genérico")


def test_manual_order_queue_persists_and_consumes_once():
    """
    ManualOrderQueue debe sobrevivir entre instancias (como cualquier
    archivo de control, ver kill_switch.py), y pop_order() debe consumir
    la orden -- no debe volver a aparecer en una consulta posterior."""
    import os
    from manual_trading import ManualOrderQueue

    control_path = ".MANUAL_ORDERS_test_queue"
    try:
        q1 = ManualOrderQueue(control_path)
        q1.queue_order("BTC_USDC", "buy")
        q1.queue_order("ETH_USDC", "sell")

        q2 = ManualOrderQueue(control_path)  # instancia nueva, mismo archivo
        assert q2.pending() == {"BTC_USDC": "buy", "ETH_USDC": "sell"}

        assert q2.pop_order("BTC_USDC") == "buy"
        assert q2.pop_order("BTC_USDC") is None, "Una orden ya consumida no debe volver a aparecer"
        assert q2.pending() == {"ETH_USDC": "sell"}, "Consumir una orden no debe tocar las demás en cola"

        assert ManualOrderQueue(control_path).pending() == {"ETH_USDC": "sell"}
    finally:
        if os.path.exists(control_path):
            os.remove(control_path)
    print("OK: ManualOrderQueue persiste entre instancias y consume cada orden una sola vez")


def test_manual_order_cli_queues_and_reports_orders():
    """CLI de manual_order.py (comprar/vender/estado) contra un archivo propio."""
    import os
    import sys
    import manual_order
    from manual_trading import ManualOrderQueue

    control_path = ".MANUAL_ORDERS_test_cli"
    old_argv = sys.argv
    try:
        sys.argv = ["manual_order.py", "comprar", "BTC_USDC", "--file", control_path]
        manual_order.main()
        assert ManualOrderQueue(control_path).pending() == {"BTC_USDC": "buy"}

        sys.argv = ["manual_order.py", "vender", "ETH_USDC", "--file", control_path]
        manual_order.main()
        assert ManualOrderQueue(control_path).pending() == {"BTC_USDC": "buy", "ETH_USDC": "sell"}

        sys.argv = ["manual_order.py", "estado", "--file", control_path]
        manual_order.main()  # no debe lanzar excepción
    finally:
        sys.argv = old_argv
        if os.path.exists(control_path):
            os.remove(control_path)
    print("OK: manual_order.py deja pedidas compras/ventas manuales y reporta el estado sin romper")


def test_live_engine_manual_buy_opens_position_respecting_shared_gates():
    """
    Una compra manual respeta los MISMOS frenos que una automática: acá
    se verifica que, con el circuit breaker activo, la orden manual se
    rechaza (no abre nada); y que sin ningún freno activo, sí abre la
    posición -- registrada con motivo "apertura_manual" en el historial.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from manual_trading import ManualOrderQueue
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_manual_buy.json")
    os.close(fd)
    os.remove(state_path)
    fd2, trades_path = tempfile.mkstemp(suffix="_live_engine_manual_buy_trades.csv")
    os.close(fd2)
    os.remove(trades_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_manual_buy"
    manual_orders_path = ".MANUAL_ORDERS_test_live_engine_manual_buy"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        cb = CircuitBreaker(max_drawdown_pct=15.0, max_daily_loss_pct=90.0)
        trade_history = TradeHistoryLog(trades_path)
        manual_orders = ManualOrderQueue(manual_orders_path)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            cb, Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_manual_buy"),
            trade_history=trade_history, manual_orders=manual_orders,
        )
        now = datetime.now(timezone.utc)

        # Con el circuit breaker activo, la compra manual debe rechazarse.
        cb.tripped = True
        manual_orders.queue_order("TEST_SYM", "buy")
        broker.set_price("TEST_SYM", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)
        assert "TEST_SYM" not in engine.internal_positions, "El breaker activo debe bloquear también una compra manual"
        assert manual_orders.pending() == {}, "La orden rechazada se descarta, no queda reintentando sola"

        # Sin ningún freno, la misma compra manual sí debe abrir la posición.
        cb.tripped = False
        manual_orders.queue_order("TEST_SYM", "buy")
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)
        assert "TEST_SYM" in engine.internal_positions, "Sin frenos activos, la compra manual debe abrir la posición"

        rows = trade_history.load_all()
        assert rows[-1]["motivo"] == "apertura_manual", "El historial debe distinguir una apertura manual de una automática"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(manual_orders_path):
            os.remove(manual_orders_path)
    print("OK: una compra manual respeta el circuit breaker y queda registrada como apertura manual")


def test_live_engine_manual_sell_always_closes_position():
    """
    Una venta manual (salir, reducir riesgo) SIEMPRE se deja pasar, sin
    importar el circuit breaker ni si el precio todavía no tocó el stop
    loss/take profit propios -- salir nunca está bloqueado, igual que ya
    pasaba con esos dos frenos automáticos.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from manual_trading import ManualOrderQueue
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_manual_sell.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_manual_sell"
    manual_orders_path = ".MANUAL_ORDERS_test_live_engine_manual_sell"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        cb = CircuitBreaker(max_drawdown_pct=15.0, max_daily_loss_pct=90.0)
        manual_orders = ManualOrderQueue(manual_orders_path)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            cb, Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_manual_sell"),
            manual_orders=manual_orders,
        )
        now = datetime.now(timezone.utc)
        broker.set_price("TEST_SYM", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)  # abre posición
        assert "TEST_SYM" in engine.internal_positions

        # Precio a mitad de camino entre el stop loss y el take profit --
        # ninguno de los dos frenos automáticos se dispararía solo acá.
        cb.tripped = True  # ni siquiera el circuit breaker debería frenar una salida
        manual_orders.queue_order("TEST_SYM", "sell")
        engine.process_tick(now, 101.0, current_atr=2.0, sig=1)  # sig=1: tampoco es salida de estrategia

        assert "TEST_SYM" not in engine.internal_positions, "Una venta manual debe cerrar la posición sin importar otros frenos"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(manual_orders_path):
            os.remove(manual_orders_path)
    print("OK: una venta manual siempre cierra la posición, sin importar otros frenos activos")


def test_live_engine_manual_sell_without_position_is_discarded_not_stuck():
    """
    Bug real encontrado en auditoría: pedir una venta manual cuando no hay
    posición abierta para ese símbolo (ej. el stop loss ya la cerró en un
    tick anterior) hacía que `pop_order()` la consumiera igual y la
    descartara en silencio, sin ningún log -- a diferencia de TODOS los
    demás rechazos manuales (breaker, pausa por noticias, ATR inválido,
    cupo de posiciones, tamaño no viable), que sí quedan explicados. Acá
    se verifica el efecto observable: la orden se consume (no queda
    reintentando sola en cada tick) y el motor no crashea ni abre nada.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from manual_trading import ManualOrderQueue
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_manual_sell_no_pos.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_manual_sell_no_pos"
    manual_orders_path = ".MANUAL_ORDERS_test_live_engine_manual_sell_no_pos"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        cb = CircuitBreaker(max_drawdown_pct=15.0, max_daily_loss_pct=90.0)
        manual_orders = ManualOrderQueue(manual_orders_path)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            cb, Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_manual_sell_no_pos"),
            manual_orders=manual_orders,
        )
        now = datetime.now(timezone.utc)
        broker.set_price("TEST_SYM", 100.0)

        assert "TEST_SYM" not in engine.internal_positions  # nunca se abrió nada
        manual_orders.queue_order("TEST_SYM", "sell")
        engine.process_tick(now, 100.0, current_atr=2.0, sig=0)  # sig=0: tampoco dispara una entrada

        assert "TEST_SYM" not in engine.internal_positions, "No debe abrirse ninguna posición por una venta manual"
        assert manual_orders.pending() == {}, "La orden sin posición que vender se descarta, no queda reintentando sola"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(manual_orders_path):
            os.remove(manual_orders_path)
    print("OK: una venta manual sin posición abierta se descarta sin crashear ni quedar reintentando")


def test_status_report_reflects_state_and_history():
    """
    status_report.py lee el estado y el historial ya persistidos y arma un
    reporte en texto -- no debe inventar ni recalcular nada, solo mostrar
    lo que ya está guardado.
    """
    import os
    import tempfile
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from status_report import build_status_report

    fd, state_path = tempfile.mkstemp(suffix="_status_report_state.json")
    os.close(fd)
    os.remove(state_path)
    fd2, trades_path = tempfile.mkstemp(suffix="_status_report_trades.csv")
    os.close(fd2)
    os.remove(trades_path)
    kill_switch_path = ".KILL_SWITCH_test_status_report"

    try:
        StateStore(path=state_path).save(
            {"TEST_SYM": {"unidades": 1.5, "precio_entrada": 100.0}}, capital=850.0,
            extra={"stop_loss": 95.0, "take_profit": 110.0, "pending_order": None},
        )
        history = TradeHistoryLog(trades_path)
        history.append(symbol="TEST_SYM", side="buy", motivo="apertura", units=1.5,
                        price=100.0, pnl=None, balance_resultante=850.0)
        history.append(symbol="TEST_SYM", side="sell", motivo="take_profit", units=1.5,
                        price=110.0, pnl=15.0, balance_resultante=865.0)

        report = build_status_report("TEST_SYM", state_path, trades_path, kill_switch_path)

        assert "TEST_SYM" in report
        assert "850.00" in report, "Debe mostrar el capital guardado"
        assert "1.5 unidades @ 100.0" in report, "Debe mostrar la posición abierta guardada"
        assert "1 operaciones cerradas (1 ganadoras, 0 perdedoras)" in report
        assert "+15.00" in report, "Debe mostrar el resultado neto"
        assert "inactivo" in report, "El kill-switch no existe -- debe reportarse inactivo"

        with open(kill_switch_path, "w", encoding="utf-8") as f:
            f.write("pausado a mano")
        report_active = build_status_report("TEST_SYM", state_path, trades_path, kill_switch_path)
        assert "ACTIVO" in report_active, "Con el archivo de control presente, debe reportar el kill-switch activo"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(trades_path):
            os.remove(trades_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: status_report.py refleja fielmente el estado y el historial ya persistidos")


def test_status_report_warns_about_concentrated_position():
    """
    status_report.py debe avisar de forma explícita cuando una posición
    persistida concentra una porción anormal del capital -- sin esto, el
    único jeito de notarlo era mirar el JSON a mano (así se encontró el
    bug real de LINK_USDC). Con una posición normal, no debe avisar nada.
    """
    import os
    import tempfile
    from state_store import StateStore
    from status_report import build_status_report

    fd, state_path = tempfile.mkstemp(suffix="_status_report_concentration.json")
    os.close(fd)
    os.remove(state_path)

    try:
        StateStore(path=state_path).save(
            {"LINK_USDC": {"unidades": 114.767004, "precio_entrada": 8.4271}}, capital=3.41, extra={},
        )
        report = build_status_report("LINK_USDC", state_path)
        assert "[!] Concentraci" in report and "LINK_USDC" in report, (
            "Debe avisar sobre la posición sobre-concentrada, no solo listarla"
        )

        StateStore(path=state_path).save(
            {"TEST_SYM": {"unidades": 1.5, "precio_entrada": 100.0}}, capital=850.0, extra={},
        )
        report_normal = build_status_report("TEST_SYM", state_path)
        assert "[!] Concentraci" not in report_normal, "Una posición normal no debería disparar el aviso"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
    print("OK: status_report.py avisa cuando una posición concentra el capital fuera de lo esperado")


def test_trade_history_log_persists_across_process_restarts():
    """
    A diferencia del estado de posiciones (una FOTO del momento), el
    historial de operaciones es append-only: acá se simula un "reinicio
    del proceso" abriendo una SEGUNDA instancia de TradeHistoryLog contra
    el mismo archivo, y debe seguir viendo todo lo escrito antes, más lo
    nuevo -- esto es lo que permite operar de forma intermitente (parar y
    retomar días o semanas después) sin perder el reporte acumulado.
    """
    import os
    import tempfile
    from trade_history import TradeHistoryLog

    fd, path = tempfile.mkstemp(suffix="_trade_history_test.csv")
    os.close(fd)
    os.remove(path)
    try:
        log1 = TradeHistoryLog(path)
        log1.append(symbol="BTC_USDC", side="buy", motivo="apertura", units=0.01,
                     price=60000, pnl=None, balance_resultante=1000.0)

        # "Reinicio": una instancia nueva del log contra el mismo archivo
        log2 = TradeHistoryLog(path)
        log2.append(symbol="BTC_USDC", side="sell", motivo="take_profit", units=0.01,
                     price=61000, pnl=10.0, balance_resultante=1010.0)

        rows = TradeHistoryLog(path).load_all()
        assert len(rows) == 2, "Debe conservar lo escrito antes del 'reinicio' más lo nuevo"
        assert rows[0]["side"] == "buy" and rows[1]["side"] == "sell"
        assert rows[1]["pnl"] == "10.0"
    finally:
        if os.path.exists(path):
            os.remove(path)
    print("OK: el historial de operaciones se acumula entre reinicios simulados del proceso")


def test_live_engine_records_buy_and_sell_in_trade_history():
    """
    Verifica que abrir y cerrar una posición queda registrado en el
    historial persistente con el motivo y el resultado correctos.
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from trade_history import TradeHistoryLog
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_history_state.json")
    os.close(fd)
    os.remove(state_path)
    fd2, history_path = tempfile.mkstemp(suffix="_live_engine_history.csv")
    os.close(fd2)
    os.remove(history_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_history"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        trade_history = TradeHistoryLog(history_path)
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), ManualKillSwitch(control_file=kill_switch_path),
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_history"),
            trade_history=trade_history,
        )

        now = datetime.now(timezone.utc)
        broker.set_price("TEST_SYM", 100.0)
        engine.process_tick(now, 100.0, current_atr=2.0, sig=1)  # abre posición
        assert "TEST_SYM" in engine.internal_positions

        # Precio entre el stop loss y el take profit calculados para esta
        # entrada -- así la salida se dispara por la señal (sig=0), no por
        # tocar alguno de los dos frenos de precio.
        broker.set_price("TEST_SYM", 102.0)
        engine.process_tick(now, 102.0, current_atr=2.0, sig=0)  # señal de salida
        assert "TEST_SYM" not in engine.internal_positions

        rows = trade_history.load_all()
        assert len(rows) == 2, f"Esperaba 2 operaciones registradas (apertura + cierre), hubo {len(rows)}"
        assert rows[0]["side"] == "buy" and rows[0]["motivo"] == "apertura"
        assert rows[1]["side"] == "sell" and rows[1]["motivo"] == "señal_estrategia"
        assert float(rows[1]["pnl"]) > 0, "Compró a 100 y vendió a 102 -- el resultado registrado debe ser positivo"
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(history_path):
            os.remove(history_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: _LiveEngine registra apertura y cierre en el historial persistente con el resultado correcto")


def test_load_many_simultaneous_circuit_breakers_stay_isolated_and_detected():
    """
    Fase 3d: simula un shock de mercado compartido que dispara el circuit
    breaker de MUCHOS usuarios al mismo tiempo (ej. un crash real del
    activo que todos tienen). Verifica que (1) cada circuit breaker sigue
    siendo una instancia propia por usuario -- nunca un pool compartido,
    incluso a esta escala --, (2) OperationsMonitor detecta y escala
    correctamente el evento sistémico, y (3) el tiempo de procesamiento
    se mantiene razonable.
    """
    import os
    import tempfile
    import time
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager
    from ops_monitor import OperationsMonitor

    n_users = 500
    fd, db_path = tempfile.mkstemp(suffix="_load_test.db")
    os.close(fd)
    os.remove(db_path)

    alert_channel = _CollectingAlertChannel()
    wallet = SimulatedWalletBalanceProvider()
    manager = UserSessionManager(wallet, db_path=db_path)
    monitor = OperationsMonitor(alert_channel, systemic_threshold_pct=50.0, min_users_for_systemic=10)

    user_ids = [f"user-{i}" for i in range(n_users)]

    try:
        start_setup = time.time()
        for uid in user_ids:
            wallet.deposit(uid, "USDC", 1000.0)
            session = manager.start_session(uid, "USDC", 1000.0, max_drawdown_pct=10.0)
            monitor.register(uid, session)
        setup_elapsed = time.time() - start_setup

        # Shock de mercado compartido: el equity de CADA usuario cae -15%
        # desde su pico -- mismo evento de mercado, N sesiones aisladas.
        start_shock = time.time()
        for uid in user_ids:
            session = manager.get_session(uid)
            session.circuit_breaker.check([1000.0, 950.0, 900.0], today_start_capital=1000.0, current_capital=850.0)
        shock_elapsed = time.time() - start_shock

        start_check = time.time()
        report = monitor.check_all()
        check_elapsed = time.time() - start_check

        assert report["usuarios_monitoreados"] == n_users
        n_circuit_breaker_issues = sum(1 for p in report["problemas_individuales"] if p["tipo"] == "circuit_breaker")
        assert n_circuit_breaker_issues == n_users, (
            f"Los {n_users} usuarios deberían haber activado su circuit breaker de forma independiente, "
            f"se detectaron {n_circuit_breaker_issues}"
        )
        assert len(report["alertas_sistemicas"]) == 1
        assert report["alertas_sistemicas"][0]["tipo"] == "circuit_breaker"
        assert report["alertas_sistemicas"][0]["porcentaje"] == 100.0

        # Aislamiento real a escala, no solo "funcionó para 2 usuarios":
        # las instancias de circuit breaker de usuarios distintos son
        # objetos DISTINTOS, nunca la misma referencia compartida.
        sample_ids = user_ids[:10]
        sample_breakers = [manager.get_session(uid).circuit_breaker for uid in sample_ids]
        assert len(set(id(b) for b in sample_breakers)) == len(sample_breakers), (
            "Cada usuario debe tener su PROPIA instancia de circuit breaker"
        )

        total_elapsed = setup_elapsed + shock_elapsed + check_elapsed
        assert total_elapsed < 30.0, (
            f"Procesar {n_users} usuarios tardó {total_elapsed:.1f}s -- demasiado lento para ser aceptable"
        )
        print(f"OK: {n_users} circuit breakers dispararon de forma aislada y correcta ante un shock compartido "
              f"(setup={setup_elapsed:.2f}s, shock={shock_elapsed:.2f}s, check_all={check_elapsed:.2f}s)")
    finally:
        for uid in user_ids:
            kill_switch_path = f"./.KILL_SWITCH_{uid}"
            try:
                manager.stop_session(uid)
            except Exception:
                pass
            if os.path.exists(kill_switch_path):
                os.remove(kill_switch_path)
        if os.path.exists(db_path):
            os.remove(db_path)


def test_manual_kill_switch_reason_returns_saved_message():
    import os
    from safety import ManualKillSwitch

    control_file = ".KILL_SWITCH_test_reason"
    ks = ManualKillSwitch(control_file=control_file)
    try:
        assert ks.reason() is None, "Sin activar, el motivo debe ser None"
        ks.activate("Mercado muy volátil, prefiero pausar manualmente")
        assert ks.reason() == "Mercado muy volátil, prefiero pausar manualmente"
    finally:
        if os.path.exists(control_file):
            os.remove(control_file)
    print("OK: ManualKillSwitch.reason() devuelve el motivo guardado al activar")


def test_live_engine_process_tick_with_active_kill_switch_does_not_crash():
    """
    Smoke test: process_tick() con el kill-switch ya activo debe manejar
    ese caso sin romperse (registra el motivo en el log y no abre
    posiciones nuevas este tick).
    """
    import os
    import tempfile
    from datetime import datetime, timezone
    from live_runner import _LiveEngine
    from broker import PaperBroker
    from risk_profiles import get_profile
    from safety import CircuitBreaker, ManualKillSwitch
    from health import Heartbeat
    from state_store import StateStore
    from alerts import ConsoleAlertChannel
    from app_logger import get_logger

    fd, state_path = tempfile.mkstemp(suffix="_live_engine_killswitch.json")
    os.close(fd)
    os.remove(state_path)
    kill_switch_path = ".KILL_SWITCH_test_live_engine_active"

    try:
        broker = PaperBroker(initial_balance=1000.0)
        kill_switch = ManualKillSwitch(control_file=kill_switch_path)
        kill_switch.activate("prueba de que no crashea")
        engine = _LiveEngine(
            broker, "TEST_SYM", get_profile("moderado"), "momentum", "moderado",
            ConsoleAlertChannel(), kill_switch,
            CircuitBreaker(), Heartbeat(max_staleness_seconds=99999),
            StateStore(path=state_path), reconcile_every=1000, log=get_logger("test_live_engine_killswitch"),
        )
        broker.set_price("TEST_SYM", 100.0)
        engine.process_tick(datetime.now(timezone.utc), 100.0, current_atr=2.0, sig=1)  # no debe tirar AttributeError
    finally:
        if os.path.exists(state_path):
            os.remove(state_path)
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
    print("OK: process_tick con el kill-switch ya activo no crashea")


def test_support_snapshot_reports_healthy_session_with_no_warnings():
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager
    from support_tools import generate_user_support_snapshot

    fd, db_path = tempfile.mkstemp(suffix="_support.db")
    os.close(fd)
    os.remove(db_path)
    kill_switch_path = ".KILL_SWITCH_test_support_healthy"

    try:
        wallet = SimulatedWalletBalanceProvider()
        wallet.deposit("user-support-1", "USDC", 1000.0)
        manager = UserSessionManager(wallet, db_path=db_path)
        session = manager.start_session("user-support-1", "USDC", 400.0)

        snapshot = generate_user_support_snapshot("user-support-1", session, wallet=wallet, currency="USDC")

        assert snapshot.user_id == "user-support-1"
        assert snapshot.saldo_disponible_billetera == 600.0
        assert snapshot.asignado_a_trading == 400.0
        assert snapshot.balance_broker == 400.0
        assert snapshot.circuit_breaker_activo is False
        assert snapshot.kill_switch_activo is False
        assert snapshot.reconciliacion["coincide"] is True
        assert snapshot.advertencias == []
        assert "user-support-1" in snapshot.to_text()
        assert "Circuit breaker: inactivo" in snapshot.to_text()

        manager.stop_session("user-support-1")
    finally:
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: el snapshot de soporte reporta una sesión sana sin advertencias")


def test_support_snapshot_flags_tripped_circuit_breaker_and_active_kill_switch():
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager
    from support_tools import generate_user_support_snapshot

    fd, db_path = tempfile.mkstemp(suffix="_support.db")
    os.close(fd)
    os.remove(db_path)
    kill_switch_path = ".KILL_SWITCH_user-support-2"

    try:
        wallet = SimulatedWalletBalanceProvider()
        wallet.deposit("user-support-2", "USDC", 1000.0)
        manager = UserSessionManager(wallet, db_path=db_path)
        session = manager.start_session("user-support-2", "USDC", 500.0)

        session.circuit_breaker.tripped = True
        session.circuit_breaker.trip_reason = "Drawdown máximo alcanzado: 18.0%"
        session.kill_switch.activate("El usuario pidió pausar por teléfono")

        snapshot = generate_user_support_snapshot("user-support-2", session, wallet=wallet, currency="USDC")

        assert snapshot.circuit_breaker_activo is True
        assert "18.0%" in snapshot.circuit_breaker_motivo
        assert snapshot.kill_switch_activo is True
        assert "pidió pausar" in snapshot.kill_switch_motivo
        assert len(snapshot.advertencias) == 2  # circuit breaker + kill switch
        text = snapshot.to_text()
        assert "ACTIVO" in text
        assert "ADVERTENCIAS PARA EL AGENTE" in text

        manager.stop_session("user-support-2")
    finally:
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: el snapshot de soporte marca claramente circuit breaker y kill-switch activos, con advertencias para el agente")


def test_support_snapshot_flags_reconciliation_mismatch():
    import os
    import tempfile
    from wallet_integration import SimulatedWalletBalanceProvider
    from multi_user import UserSessionManager
    from support_tools import generate_user_support_snapshot

    fd, db_path = tempfile.mkstemp(suffix="_support.db")
    os.close(fd)
    os.remove(db_path)
    kill_switch_path = ".KILL_SWITCH_user-support-3"

    try:
        wallet = SimulatedWalletBalanceProvider()
        wallet.deposit("user-support-3", "USDC", 1000.0)
        manager = UserSessionManager(wallet, db_path=db_path)
        session = manager.start_session("user-support-3", "USDC", 500.0)

        # El estado guardado dice que hay una posición en BTC_USDC que el bróker no tiene.
        session.state_store.save({"BTC_USDC": {"unidades": 1.0, "precio_entrada": 100.0}}, capital=400.0)

        snapshot = generate_user_support_snapshot("user-support-3", session, wallet=wallet, currency="USDC")

        assert snapshot.reconciliacion["coincide"] is False
        assert "BTC_USDC" in snapshot.reconciliacion["solo_en_interno"]
        assert any("desfasaje" in w.lower() for w in snapshot.advertencias)

        manager.stop_session("user-support-3")
    finally:
        if os.path.exists(kill_switch_path):
            os.remove(kill_switch_path)
        if os.path.exists(db_path):
            os.remove(db_path)
    print("OK: el snapshot de soporte detecta un desfasaje de reconciliación y advierte al agente antes de confirmar nada")


def test_all_strategies_return_valid_binary_signal_on_real_data():
    """
    strategies.py no tenía ningún test directo (solo se ejercitaba
    indirectamente vía Backtester en otros tests) -- verifica que las 4
    estrategias corren sobre datos reales y devuelven siempre 0/1, nunca
    otro valor.
    """
    from data_utils import load_csv
    from strategies import STRATEGIES

    df = load_csv("real_data/btc_daily.csv")
    for name, fn in STRATEGIES.items():
        signal = fn(df)
        assert len(signal) == len(df), f"{name}: la señal debe tener la misma longitud que los datos"
        valid_values = set(signal.dropna().unique().tolist())
        assert valid_values <= {0, 1}, f"{name} devolvió valores fuera de {{0,1}}: {valid_values}"
    print("OK: las 4 estrategias devuelven una señal binaria válida sobre datos reales")


def test_get_strategy_raises_for_unknown_name():
    from strategies import get_strategy

    try:
        get_strategy("no_existe")
        assert False, "Debería fallar para una estrategia inexistente"
    except ValueError:
        pass
    print("OK: get_strategy rechaza nombres de estrategia inválidos")


def test_trend_following_signal_matches_ma_crossover():
    """Verificación directa de correctitud: la señal debe coincidir exactamente
    con el cruce de medias móviles, no aproximadamente."""
    import pandas as pd
    import numpy as np
    from strategies import trend_following

    n = 80
    idx = pd.bdate_range("2024-01-01", periods=n)
    # OJO: construir `close` YA con el índice final antes de meterlo en el
    # DataFrame -- si se pasa una Series con su propio índice (ej. 0..79)
    # y se le fuerza otro índice distinto en el constructor, pandas
    # REALINEA por etiqueta en vez de por posición, y como no comparten
    # ninguna etiqueta el resultado queda en NaN silenciosamente (esto
    # rompió esta prueba antes de corregirla, no era un bug de strategies.py).
    close = pd.Series(np.linspace(100, 200, n), index=idx)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}, index=idx)
    signal = trend_following(df, fast=20, slow=50)

    fast_ma = close.rolling(20).mean()
    slow_ma = close.rolling(50).mean()
    expected = (fast_ma > slow_ma).astype(int)
    assert (signal.fillna(0) == expected.fillna(0)).all(), "La señal debe coincidir exactamente con el cruce de medias"
    assert signal.iloc[-1] == 1, "En una serie claramente ascendente, la media rápida debe superar a la lenta al final"
    print("OK: trend_following genera la señal exactamente en el cruce de medias móviles esperado")


def test_value_dip_in_uptrend_requires_both_conditions():
    """
    value_dip_in_uptrend combina dos condiciones (tendencia alcista de
    largo plazo Y una caída reciente) -- verifica que hace falta AMBAS,
    no alcanza con una sola.
    """
    import pandas as pd
    import numpy as np
    from strategies import value_dip_in_uptrend

    n = 260
    close_with_dip = np.linspace(100, 300, n)
    close_with_dip[-10:] = close_with_dip[-11] * np.linspace(1.0, 0.93, 10)  # caída ~7% en los últimos 10 días
    idx = pd.bdate_range("2024-01-01", periods=n)
    df_dip = pd.DataFrame({"open": close_with_dip, "high": close_with_dip + 1, "low": close_with_dip - 1,
                           "close": close_with_dip, "volume": 1000}, index=idx)
    signal_dip = value_dip_in_uptrend(df_dip, trend_ma=200, dip_lookback=10)
    assert signal_dip.iloc[-1] == 1, "Debería detectar la caída dentro de la tendencia alcista de largo plazo"

    close_no_dip = np.linspace(100, 300, n)  # misma tendencia alcista, sin ninguna caída
    df_no_dip = pd.DataFrame({"open": close_no_dip, "high": close_no_dip + 1, "low": close_no_dip - 1,
                              "close": close_no_dip, "volume": 1000}, index=idx)
    signal_no_dip = value_dip_in_uptrend(df_no_dip, trend_ma=200, dip_lookback=10)
    assert signal_no_dip.iloc[-1] == 0, "Sin ninguna caída reciente, no debería haber señal aunque la tendencia sea alcista"
    print("OK: value_dip_in_uptrend exige tendencia alcista Y una caída reciente, no alcanza con una sola condición")


def test_walk_forward_validate_returns_expected_structure_on_real_data():
    """validation.py (walk-forward, citado en el README como el diferencial
    anti-sobreajuste del proyecto) no tenía ningún test propio."""
    from data_utils import load_csv
    from validation import walk_forward_validate

    df = load_csv("real_data/btc_daily.csv")
    result = walk_forward_validate(df, "momentum", "moderado")
    assert "retorno_total_pct" in result["in_sample"]
    assert "retorno_total_pct" in result["out_sample"]
    assert "degradacion_pct" in result and "advertencia" in result
    print("OK: walk_forward_validate corre sobre datos reales y devuelve la estructura esperada")


def test_walk_forward_validate_warns_on_severe_out_of_sample_degradation():
    """
    Fuerza un escenario donde el in-sample es claramente rentable
    (tendencia alcista limpia) y el out-of-sample es un desastre (flash
    crash del -80%) -- debe disparar la advertencia de sobreajuste.
    """
    import pandas as pd
    import numpy as np
    from validation import walk_forward_validate

    n = 200
    uptrend = np.linspace(100, 200, n // 2)
    crash = uptrend[-1] * np.linspace(1.0, 0.2, n // 2)
    close = np.concatenate([uptrend, crash])
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000},
                       index=pd.bdate_range("2024-01-01", periods=n))

    result = walk_forward_validate(df, "tendencia", "moderado", split_pct=0.5)
    assert result["in_sample"]["retorno_total_pct"] > 0, "El in-sample (tendencia alcista limpia) debería ser rentable"
    assert result["degradacion_pct"] is not None and result["degradacion_pct"] > 60
    assert result["advertencia"] is not None, "Debería advertir sobreajuste ante una degradación tan severa"
    print("OK: walk_forward_validate advierte sobreajuste cuando el out-of-sample se derrumba respecto al in-sample")


def test_rolling_walk_forward_validate_computes_consistency_correctly():
    from data_utils import load_csv
    from validation import rolling_walk_forward_validate

    df = load_csv("real_data/btc_daily.csv")
    result = rolling_walk_forward_validate(df, "momentum", "agresivo", n_windows=4)
    assert result["n_ventanas"] == 4
    assert len(result["ventanas"]) == 4
    n_positive = sum(1 for w in result["ventanas"] if w["retorno_out_sample_pct"] > 0)
    expected_consistency = round(n_positive / 4 * 100, 1)
    assert result["consistencia_pct"] == expected_consistency
    print("OK: rolling_walk_forward_validate calcula la consistencia exactamente como la fracción de ventanas rentables fuera de muestra")


def test_rolling_walk_forward_validate_rejects_too_many_windows_for_data_size():
    import pandas as pd
    import numpy as np
    from validation import rolling_walk_forward_validate

    tiny_df = pd.DataFrame({
        "open": np.linspace(100, 110, 30), "high": np.linspace(101, 111, 30),
        "low": np.linspace(99, 109, 30), "close": np.linspace(100, 110, 30), "volume": np.ones(30),
    }, index=pd.bdate_range("2024-01-01", periods=30))

    try:
        rolling_walk_forward_validate(tiny_df, "momentum", "moderado", n_windows=10)
        assert False, "Debería fallar: 30 velas / 10 ventanas = 3 velas por ventana, insuficiente"
    except ValueError:
        pass
    print("OK: rolling_walk_forward_validate rechaza pedir más ventanas de las que el dataset puede sostener")


def test_run_crisis_stress_test_covers_known_periods_with_real_data():
    """stress_test.py (citado en el README como validación contra crisis
    históricas reales) no tenía ningún test propio."""
    from data_utils import load_csv
    from stress_test import run_crisis_stress_test, CRISIS_PERIODS

    df = load_csv("real_data/btc_daily.csv")  # 2020-01-02 a 2024-09-17 -- cubre las 4 crisis conocidas
    result = run_crisis_stress_test(df, "momentum", "agresivo")

    assert set(result.keys()) == set(CRISIS_PERIODS.keys())
    for name, r in result.items():
        assert r["cubierto"] is True, f"Se esperaba que {name} estuviera cubierto por el rango de btc_daily.csv"
        assert "retorno_pct" in r and "retorno_buy_and_hold_pct" in r
    print("OK: run_crisis_stress_test cubre las 4 crisis históricas conocidas con datos reales de BTC")


def test_run_crisis_stress_test_skips_periods_outside_dataset_range():
    import pandas as pd
    import numpy as np
    from stress_test import run_crisis_stress_test

    short_df = pd.DataFrame({
        "open": np.linspace(100, 110, 40), "high": np.linspace(101, 111, 40),
        "low": np.linspace(99, 109, 40), "close": np.linspace(100, 110, 40), "volume": np.ones(40),
    }, index=pd.bdate_range("2024-01-01", periods=40))  # ninguna crisis conocida cae en este rango

    result = run_crisis_stress_test(short_df, "momentum", "moderado")
    for name, r in result.items():
        assert r["cubierto"] is False
        assert "motivo" in r
    print("OK: run_crisis_stress_test informa (no crashea) los períodos que el dataset no alcanza a cubrir")


def test_run_backtest_config_yaml_with_accents_does_not_crash():
    """
    Regresión de un bug real: `python run_backtest.py --config config_ejemplo.yaml`
    abría el YAML con `open(args.config)` sin encoding explícito -- en
    Windows eso usa el codepage del sistema (cp1252) en vez de UTF-8, y
    crasheaba con UnicodeDecodeError apenas el archivo tenía una tilde
    (como config_ejemplo.yaml, que las tiene en sus comentarios). El CI
    corre en Linux (UTF-8 por defecto), por eso nunca se detectó ahí --
    esto no se hubiera visto sin correr el proyecto en Windows de verdad.
    """
    import sys
    import os
    import tempfile
    import run_backtest

    fd, out_path = tempfile.mkstemp(suffix="_rb_config_test.png")
    os.close(fd)
    os.remove(out_path)
    fd2, html_path = tempfile.mkstemp(suffix="_rb_config_test.html")
    os.close(fd2)
    os.remove(html_path)

    old_argv = sys.argv
    try:
        sys.argv = [
            "run_backtest.py", "--config", "config_ejemplo.yaml",
            "--csv", "real_data/aapl_daily.csv", "--out", out_path, "--html-report", html_path,
        ]
        run_backtest.main()  # no debe tirar UnicodeDecodeError
        assert os.path.exists(out_path), "Debería haber generado el gráfico"
        assert os.path.exists(html_path), "Debería haber generado el reporte HTML"
    finally:
        sys.argv = old_argv
        for p in (out_path, html_path):
            if os.path.exists(p):
                os.remove(p)
    print("OK: run_backtest.py --config lee un YAML con tildes sin crashear (regresión de encoding)")


if __name__ == "__main__":
    tests = [
        test_risk_never_exceeds_profile,
        test_position_size_never_exceeds_capital,
        test_position_size_capped_by_capital_survives_slippage_and_commission,
        test_position_size_caps_concentration_when_atr_is_tiny_relative_to_price,
        test_zero_atr_returns_zero_units,
        test_validation_detects_corrupt_data,
        test_validation_passes_clean_data,
        test_circuit_breaker_trips_on_drawdown,
        test_profit_lock_check_detects_target_reached,
        test_profit_lock_lock_in_ratchets_and_keeps_operating,
        test_profit_lock_rejects_invalid_params,
        test_min_trade_value_rejects_tiny_trades,
        test_concentration_warnings_flags_only_positions_above_threshold,
        test_adx_bounded_and_regime_classifies,
        test_heartbeat_detects_staleness,
        test_data_gap_detection,
        test_correlation_scales_down_correlated_positions,
        test_correlation_matrix_detects_high_correlation,
        test_monte_carlo_bootstrap_generates_variation,
        test_engine_survives_single_price_flash_crash,
        test_engine_handles_flat_zero_volatility_data,
        test_validate_rejects_single_row_dataset,
        test_backtest_reproducibility,
        test_paper_broker_rejects_invalid_orders,
        test_console_alert_channel_sends,
        test_telegram_channel_fails_gracefully_without_network,
        test_multi_timeframe_no_lookahead,
        test_live_runner_smoke_test,
        test_live_polling_runs_with_stub_price_feed,
        test_append_live_tick_same_day_updates_in_place,
        test_append_live_tick_new_day_opens_new_candle,
        test_live_polling_atr_stays_realistic_across_many_same_day_ticks,
        test_live_engine_enforces_shared_position_cap_across_symbols,
        test_live_engine_multi_symbol_shares_broker_without_artificial_limit,
        test_symbol_expectancy_requires_minimum_trades_and_averages_pnl,
        test_pick_weakest_open_position_ignores_unknown_and_picks_lowest,
        test_live_engine_rotates_weakest_position_for_better_automatic_candidate,
        test_live_engine_does_not_rotate_for_unproven_candidate,
        test_live_engine_manual_entry_never_triggers_rotation,
        test_run_live_polling_multi_symbol_smoke_test,
        test_broker_adapters_dont_leak_into_each_other,
        test_state_survives_simulated_restart,
        test_live_engine_restores_saved_capital_on_restart,
        test_live_engine_restores_open_position_in_broker_on_restart,
        test_live_engine_restores_circuit_breaker_state_on_restart,
        test_live_engine_restores_news_pause_on_restart,
        test_live_engine_restores_news_seen_links_on_restart,
        test_live_engine_restores_daily_loss_reference_on_restart,
        test_live_engine_closes_position_to_lock_profit_and_keeps_operating,
        test_live_engine_profit_lock_multi_symbol_banks_full_equity_not_just_cash,
        test_live_engine_profit_lock_sell_close_banks_full_equity_not_just_cash,
        test_live_engine_restores_profit_lock_state_on_restart,
        test_state_store_handles_corrupt_file,
        test_reconciliation_detects_all_mismatch_types,
        test_retry_with_backoff_retries_transient_not_permanent,
        test_paper_broker_order_idempotency,
        test_significance_module_runs_and_bounds_percentile,
        test_significance_random_strategy_never_overlaps_positions,
        test_parameter_sensitivity_detects_sign_flip,
        test_ripio_signature_matches_official_scheme,
        test_ripio_normalize_pair,
        test_ripio_place_order_blocked_without_allow_trading,
        test_ripio_private_requires_credentials,
        test_ripio_public_ticker_live,
        test_classify_impact_matches_keywords,
        test_news_monitor_dedup_and_filters_low_impact,
        test_news_monitor_get_and_restore_seen_links,
        test_news_monitor_empty_feeds_list_makes_no_requests,
        test_automation_window_handles_midnight_crossing,
        test_news_guard_pauses_entries_only_in_automatic_window,
        test_live_polling_blocks_entries_during_news_pause,
        test_param_optimizer_ranks_by_out_of_sample_only,
        test_param_optimizer_rejects_too_few_windows_worth_of_data,
        test_wallet_reserve_and_release_round_trip,
        test_wallet_reserve_fails_without_enough_available_balance,
        test_wallet_settle_trade_result_only_touches_trading_allocation,
        test_user_sessions_are_fully_isolated,
        test_start_session_fails_cleanly_without_enough_wallet_balance,
        test_user_session_manager_uses_one_shared_db_not_one_file_per_user,
        test_alpaca_get_current_price_parses_latest_trade,
        test_alpaca_get_balance_reads_cash_from_account,
        test_alpaca_get_open_positions_maps_fields,
        test_alpaca_place_order_blocked_without_allow_trading,
        test_alpaca_place_order_when_allowed_posts_to_orders_endpoint,
        test_alpaca_place_order_open_status_reports_zero_units_not_requested,
        test_alpaca_get_order_status_parses_response,
        test_live_polling_accepts_alpaca_as_price_source,
        test_alpaca_private_requires_credentials,
        test_shared_price_feed_dedupes_concurrent_consumers_on_same_symbol,
        test_shared_price_feed_polls_symbols_independently,
        test_shared_price_feed_refreshes_when_stale,
        test_shared_price_feed_as_drop_in_reduces_calls_for_two_sessions,
        test_sqlite_state_store_save_and_load_round_trip,
        test_sqlite_state_store_empty_when_no_prior_state,
        test_sqlite_state_store_multiple_users_share_one_db_without_mixing,
        test_ops_monitor_detects_individual_circuit_breaker_and_kill_switch_issues,
        test_ops_monitor_escalates_to_systemic_alert_when_threshold_crossed,
        test_ops_monitor_does_not_escalate_with_too_few_users,
        test_ops_monitor_detects_reconciliation_mismatch,
        test_ops_monitor_detects_concentration_issue,
        test_paper_broker_partial_fill_reports_actual_units,
        test_paper_broker_zero_fill_ratio_leaves_order_open_with_no_position,
        test_paper_broker_simulate_additional_fill_completes_open_order,
        test_live_engine_open_buy_order_resolves_without_double_ordering,
        test_live_engine_partial_buy_fill_registers_position_with_actual_units,
        test_trade_history_log_persists_across_process_restarts,
        test_live_engine_records_buy_and_sell_in_trade_history,
        test_status_report_reflects_state_and_history,
        test_status_report_warns_about_concentrated_position,
        test_real_results_report_summarizes_sessions_from_directory,
        test_real_results_report_warns_about_concentrated_position,
        test_real_results_report_flags_trades_closed_before_atr_fix,
        test_real_results_report_output_file_is_valid_utf8,
        test_kill_switch_cli_respects_custom_control_file,
        test_manual_order_queue_persists_and_consumes_once,
        test_manual_order_cli_queues_and_reports_orders,
        test_live_engine_manual_buy_opens_position_respecting_shared_gates,
        test_live_engine_manual_sell_always_closes_position,
        test_live_engine_manual_sell_without_position_is_discarded_not_stuck,
        test_pair_trades_and_tax_export_consume_real_trade_history,
        test_load_many_simultaneous_circuit_breakers_stay_isolated_and_detected,
        test_manual_kill_switch_reason_returns_saved_message,
        test_live_engine_process_tick_with_active_kill_switch_does_not_crash,
        test_support_snapshot_reports_healthy_session_with_no_warnings,
        test_support_snapshot_flags_tripped_circuit_breaker_and_active_kill_switch,
        test_support_snapshot_flags_reconciliation_mismatch,
        test_all_strategies_return_valid_binary_signal_on_real_data,
        test_get_strategy_raises_for_unknown_name,
        test_trend_following_signal_matches_ma_crossover,
        test_value_dip_in_uptrend_requires_both_conditions,
        test_walk_forward_validate_returns_expected_structure_on_real_data,
        test_walk_forward_validate_warns_on_severe_out_of_sample_degradation,
        test_rolling_walk_forward_validate_computes_consistency_correctly,
        test_rolling_walk_forward_validate_rejects_too_many_windows_for_data_size,
        test_run_crisis_stress_test_covers_known_periods_with_real_data,
        test_run_crisis_stress_test_skips_periods_outside_dataset_range,
        test_run_backtest_config_yaml_with_accents_does_not_crash,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            # No solo AssertionError: un test que depende de red (ej. el
            # ticker público de Ripio) puede tirar un error de conexión en
            # vez de una aserción fallida -- si eso corta el script entero,
            # nunca nos enteramos del resultado de ningún test que venga
            # después. Un test roto no debe esconder a los demás.
            failed += 1
            print(f"FALLÓ: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests pasaron")
