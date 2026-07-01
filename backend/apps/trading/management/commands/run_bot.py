import time
import datetime
import pandas as pd
import yfinance as yf
from django.core.management.base import BaseCommand
from apps.account.models import Account
from core.broker.kis.broker import KoreaInvestmentBroker
from core.ml_service import MLFilterService
import sys
import os

# Strategy imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../')))
from research.strategies.sample_strategies import IntradayDojiVolumeStrategy, MovingAverageBreakoutStrategy


class Command(BaseCommand):
    help = 'Run the live trading bot with ML Filter'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS("🚀 Starting Golden Age Live Trading Bot with ML Filter (Threshold 0.50)..."))
        
        # 1. 모의투자 계좌 로드 (Paper)
        account = Account.objects.filter(account_type=Account.AccountType.PAPER).first()
        if not account:
            self.stdout.write(self.style.ERROR("❌ No PAPER account found in DB. Please create one first."))
            return
            
        broker = KoreaInvestmentBroker(account)
        ml_service = MLFilterService()
        
        # 전략 세팅 (1위 조합)
        stg_doji = IntradayDojiVolumeStrategy(vol_mult=3.0)
        stg_ma = MovingAverageBreakoutStrategy(window=60)
        weight_doji, weight_ma, threshold = 0.4, 0.6, 20.0
        ml_threshold = 0.50
        
        # 상태 관리
        # positions[symbol] = {"entry_price": float, "high_price": float, "quantity": int}
        positions = {}
        trailing_stop_pct = 0.015

        def get_hybrid_minute_data(symbol: str, yf_ticker: str) -> pd.DataFrame:
            """
            어제까지의 데이터는 yfinance(무료)로 가져오고,
            오늘의 데이터는 KIS API 실시간으로 가져와 병합합니다.
            """
            try:
                # 1. YFinance에서 과거 5일치 5분봉 가져오기
                ticker = yf.Ticker(yf_ticker)
                yf_df = ticker.history(period="5d", interval="5m")
                
                if not yf_df.empty:
                    yf_df = yf_df.reset_index()
                    yf_df = yf_df.rename(columns={
                        'Datetime': 'date', 'Open': 'open', 'High': 'high', 
                        'Low': 'low', 'Close': 'close', 'Volume': 'volume'
                    })
                    # 시간대 변환 (UTC -> Asia/Seoul)
                    if yf_df['date'].dt.tz is not None:
                        yf_df['date'] = yf_df['date'].dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
                    yf_df = yf_df[['date', 'open', 'high', 'low', 'close', 'volume']]
                else:
                    yf_df = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
                    
                # 2. KIS API에서 오늘 실시간 5분봉 가져오기
                kis_df = broker.get_minute_chart(symbol, period=5)
                
                if kis_df.empty:
                    return yf_df
                    
                # 3. 병합 (동일한 시간대는 KIS 데이터로 덮어쓰기 위해 중복 제거)
                combined = pd.concat([yf_df, kis_df]).sort_values('date')
                # 5분 단위로 반올림하여 인덱스 정렬
                combined['date_key'] = combined['date'].dt.floor('5min')
                combined = combined.drop_duplicates(subset=['date_key'], keep='last')
                combined = combined.drop(columns=['date_key']).reset_index(drop=True)
                
                return combined
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Data fetch error for {symbol}: {e}"))
                return pd.DataFrame()

        # 무한 루프 시작
        while True:
            now = datetime.datetime.now()
            market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
            market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
            force_sell_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
            
            # 장 외 시간 휴식
            if now < market_open or now > market_close:
                self.stdout.write(f"[{now.strftime('%H:%M:%S')}] 장이 열리지 않았습니다. 대기 중...")
                time.sleep(60)
                continue
                
            self.stdout.write(f"\n[{now.strftime('%H:%M:%S')}] 🤖 시장 모니터링 중...")
            
            # DB에서 타겟 종목 로드
            from apps.trading.models import Trader, TraderTargetStock
            trader = Trader.objects.filter(account=account).first()
            if not trader:
                self.stdout.write(self.style.ERROR("Trader not found in DB."))
                time.sleep(60)
                continue
                
            target_stocks = TraderTargetStock.objects.filter(trader=trader, is_active=True).select_related('stock')
            
            if not target_stocks:
                self.stdout.write("DB에 등록된 타겟 종목이 없습니다. 관리자 페이지에서 추가해주세요.")
            
            for ts in target_stocks:
                symbol = ts.stock.symbol
                yf_ticker = f"{symbol}.KS"
                ml_threshold = float(ts.ml_threshold)
                
                # 데이터 수집
                df = get_hybrid_minute_data(symbol, yf_ticker)
                if len(df) < 60:
                    continue # MA60 계산을 위해 최소 60개 필요
                    
                current_price = df.iloc[-1]['close']
                
                # 포지션 관리 로직
                if symbol in positions:
                    pos = positions[symbol]
                    pos['high_price'] = max(pos['high_price'], current_price)
                    
                    # 1. 트레일링 스탑 체크
                    if current_price < pos['high_price'] * (1.0 - trailing_stop_pct):
                        self.stdout.write(self.style.WARNING(f"[{symbol}] 📉 트레일링 스탑 발동! 매도 실행."))
                        broker.create_order(symbol, "SELL", pos['quantity'])
                        del positions[symbol]
                        continue
                        
                    # 2. 15:20 강제 청산 체크
                    if now >= force_sell_time:
                        self.stdout.write(self.style.WARNING(f"[{symbol}] ⏰ 15:20 강제 청산 발동! 매도 실행."))
                        broker.create_order(symbol, "SELL", pos['quantity'])
                        del positions[symbol]
                        continue
                        
                    # 3. 룰 기반 매도 시그널 체크
                    sig_doji = stg_doji.generate_signals(df)
                    sig_ma = stg_ma.generate_signals(df)
                    if sig_doji.iloc[-1]['decision'] == -1 or sig_ma.iloc[-1]['decision'] == -1:
                        self.stdout.write(self.style.WARNING(f"[{symbol}] 🔴 전략 매도 시그널 발생! 매도 실행."))
                        broker.create_order(symbol, "SELL", pos['quantity'])
                        del positions[symbol]
                        continue
                        
                    # 보유 중이면 추가 매수하지 않음
                    continue
                
                # 미보유 상태: 15:20 이후엔 신규 진입 불가
                if now >= force_sell_time:
                    continue
                    
                # 매수 로직 (신규 진입)
                sig_doji = stg_doji.generate_signals(df)
                sig_ma = stg_ma.generate_signals(df)
                
                dec_doji = sig_doji.iloc[-1]['decision']
                score_doji = sig_doji.iloc[-1]['score']
                dec_ma = sig_ma.iloc[-1]['decision']
                score_ma = sig_ma.iloc[-1]['score']
                
                mixed_score = (float(dec_doji or 0) * float(score_doji or 0) * weight_doji) + \
                              (float(dec_ma or 0) * float(score_ma or 0) * weight_ma)
                              
                if mixed_score > threshold:
                    self.stdout.write(f"[{symbol}] 🟡 1차 매수 시그널 검출 (점수: {mixed_score:.2f}). ML 필터링 시작...")
                    
                    # ML Filter 추론
                    ml_prob = ml_service.predict_prob(df)
                    
                    if ml_prob >= ml_threshold:
                        self.stdout.write(self.style.SUCCESS(f"[{symbol}] 🟢 ML 필터 통과! (승률 {ml_prob*100:.1f}% >= {ml_threshold*100:.1f}%). 시장가 매수 실행!"))
                        
                        # 투자금 100만원 가정 (또는 예수금 비례)
                        order_qty = int(1000000 / current_price)
                        res = broker.create_order(symbol, "BUY", order_qty)
                        
                        if res.success:
                            positions[symbol] = {
                                "entry_price": current_price,
                                "high_price": current_price,
                                "quantity": order_qty
                            }
                        else:
                            self.stdout.write(self.style.ERROR(f"[{symbol}] 주문 실패: {res.error_message}"))
                    else:
                        self.stdout.write(f"[{symbol}] 🛑 ML 기각 (승률 {ml_prob*100:.1f}% < {ml_threshold*100:.1f}%). 휩쏘(가짜)로 판단하여 무시합니다.")

            # 5분 주기에 맞추어 적절히 대기 (보통 분봉 확정 시점을 위해 매 5분 정각을 노림. 여기선 간소화)
            time.sleep(60)

