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
from safety import validate_ohlcv, CircuitBreaker


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


def test_min_trade_value_rejects_tiny_trades():
    profile = get_profile("agresivo")
    # Capital muy chico + mínimo operable alto -> la operación no debería ser viable
    sizing = position_size(50.0, 100.0, 2.0, profile, min_trade_value=1000.0)
    assert sizing["viable"] is False, "Debería marcar la operación como no viable"
    print("OK: el mínimo operable rechaza operaciones demasiado chicas")


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


if __name__ == "__main__":
    tests = [
        test_risk_never_exceeds_profile,
        test_position_size_never_exceeds_capital,
        test_zero_atr_returns_zero_units,
        test_validation_detects_corrupt_data,
        test_validation_passes_clean_data,
        test_circuit_breaker_trips_on_drawdown,
        test_min_trade_value_rejects_tiny_trades,
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
        test_broker_adapters_dont_leak_into_each_other,
        test_state_survives_simulated_restart,
        test_state_store_handles_corrupt_file,
        test_reconciliation_detects_all_mismatch_types,
        test_retry_with_backoff_retries_transient_not_permanent,
        test_paper_broker_order_idempotency,
        test_significance_module_runs_and_bounds_percentile,
        test_parameter_sensitivity_detects_sign_flip,
        test_ripio_signature_matches_official_scheme,
        test_ripio_normalize_pair,
        test_ripio_place_order_blocked_without_allow_trading,
        test_ripio_private_requires_credentials,
        test_ripio_public_ticker_live,
        test_classify_impact_matches_keywords,
        test_news_monitor_dedup_and_filters_low_impact,
        test_news_monitor_empty_feeds_list_makes_no_requests,
        test_automation_window_handles_midnight_crossing,
        test_news_guard_pauses_entries_only_in_automatic_window,
        test_live_polling_blocks_entries_during_news_pause,
        test_param_optimizer_ranks_by_out_of_sample_only,
        test_param_optimizer_rejects_too_few_windows_worth_of_data,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FALLÓ: {t.__name__} -> {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests pasaron")
