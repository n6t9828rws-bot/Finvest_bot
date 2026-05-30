"""
FinVest Agent Bot v5
pip install python-telegram-bot requests yfinance reportlab
python finvest_bot_v5.py
"""

import logging, json, os, io, asyncio
from datetime import datetime, time as dtime, timedelta
import yfinance as yf
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ══════════════════════════════════════════════
# No Railway: defina a variável de ambiente BOT_TOKEN.
# Localmente: troque "SEU_TOKEN_AQUI" pelo seu token.
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI")
CONFIG_FILE  = "config_v4.json"
TAREFAS_FILE = "tarefas_v4.json"
ALERTAS_FILE = "alertas_v4.json"
# ══════════════════════════════════════════════

TICKERS = {
    "ibovespa":"^BVSP",  "dolar":"USDBRL=X",
    "sp500":"^GSPC",     "nasdaq":"^IXIC",
    "dow":"^DJI",        "bitcoin":"BTC-USD",
    "brent":"BZ=F",      "ouro":"GC=F",
    "petr4":"PETR4.SA",  "vale3":"VALE3.SA",
    "itub4":"ITUB4.SA",  "bbdc4":"BBDC4.SA",
    "abev3":"ABEV3.SA",  "wege3":"WEGE3.SA",
}
TAXAS = {"selic": 14.75, "cdi": 14.65}

# ══════════════════════════════════════════════
# FETCH PARALELO
# ══════════════════════════════════════════════
def get_quote_sync(symbol):
    try:
        t = yf.Ticker(symbol); i = t.fast_info
        price = i.last_price; prev = i.previous_close
        chg = ((price-prev)/prev*100) if prev else 0
        return {"price":round(price,4),"change":round(chg,2),
                "low":round(i.day_low or price,4),"high":round(i.day_high or price,4)}
    except: return {"price":None,"change":None,"low":None,"high":None}

def get_news_sync(symbol, limit=4):
    try:
        t = yf.Ticker(symbol)
        news_out = []
        for n in (t.news or [])[:limit]:
            content = n.get("content", {})
            title   = content.get("title") or n.get("title","")
            pub     = content.get("pubDate") or n.get("providerPublishTime","")
            source  = ""
            if isinstance(content.get("provider"), dict):
                source = content["provider"].get("displayName","")
            date_str = ""
            if isinstance(pub, (int,float)):
                date_str = datetime.fromtimestamp(pub).strftime("%d/%m %H:%M")
            elif isinstance(pub, str) and pub:
                try: date_str = datetime.fromisoformat(pub.replace("Z","")).strftime("%d/%m %H:%M")
                except: date_str = pub[:10]
            if title:
                news_out.append({"title": title[:120], "date": date_str, "source": source})
        return news_out
    except: return []

async def get_quote_async(key, symbol):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, get_quote_sync, symbol)
    return key, result

async def fetch_all_fast():
    tasks = [get_quote_async(k, v) for k,v in TICKERS.items()]
    results = await asyncio.gather(*tasks)
    data = dict(results)
    data["selic"] = TAXAS["selic"]; data["cdi"] = TAXAS["cdi"]
    data["updated_at"] = datetime.now().strftime("%H:%M:%S")
    return data

async def fetch_news_async():
    loop = asyncio.get_event_loop()
    news_br  = await loop.run_in_executor(None, get_news_sync, "^BVSP", 5)
    news_petr= await loop.run_in_executor(None, get_news_sync, "PETR4.SA", 3)
    news_vale= await loop.run_in_executor(None, get_news_sync, "VALE3.SA", 3)
    news_int = await loop.run_in_executor(None, get_news_sync, "^GSPC", 4)
    news_btc = await loop.run_in_executor(None, get_news_sync, "BTC-USD", 3)
    # Deduplica
    seen = set(); all_news = []
    for n in news_br + news_petr + news_vale + news_int + news_btc:
        if n["title"] not in seen:
            seen.add(n["title"]); all_news.append(n)
    return all_news[:12]

# ══════════════════════════════════════════════
# FORMATAÇÃO
# ══════════════════════════════════════════════
def fmt(val, dec=2, pfx=""):
    if val is None: return "—"
    try:
        s = f"{float(val):,.{dec}f}".replace(",","X").replace(".",",").replace("X",".")
        return pfx + s
    except: return "—"

def fc(val):
    if val is None: return "—"
    try: v=float(val); return ("+" if v>=0 else "")+f"{v:.2f}%"
    except: return "—"

def arrow(val):
    if val is None: return "⚪"
    return "🟢" if val >= 0 else "🔴"

def load_json(f, default):
    if os.path.exists(f):
        with open(f) as fp: return json.load(fp)
    return default

def save_json(f, data):
    with open(f,"w") as fp: json.dump(data, fp, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════
# CALENDÁRIO ECONÔMICO
# ══════════════════════════════════════════════
def get_calendario():
    hoje = datetime.now()
    # Dia da semana: 0=seg, 6=dom
    dia = hoje.weekday()
    inicio_semana = hoje - timedelta(days=dia)

    eventos_fixos = [
        # (dia_semana, hora, pais, evento, impacto)
        (0, "09:00", "🇧🇷", "Abertura B3",           "baixo"),
        (1, "08:30", "🇺🇸", "Confiança do Consumidor","médio"),
        (2, "09:00", "🇧🇷", "IPCA-15 (se semana)",   "alto"),
        (2, "14:00", "🇺🇸", "Ata do Fed (se semana)","alto"),
        (3, "08:30", "🇺🇸", "PIB EUA (se trimestre)", "alto"),
        (3, "09:00", "🇧🇷", "Produção Industrial",   "médio"),
        (4, "08:30", "🇺🇸", "Payroll (1ª sexta)",    "alto"),
        (4, "09:00", "🇧🇷", "Resultado Primário",    "médio"),
    ]

    semana = []
    dias_pt = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
    for d, hora, pais, evento, impacto in eventos_fixos:
        data_evento = inicio_semana + timedelta(days=d)
        if data_evento.weekday() >= 5: continue  # pula fim de semana
        passou = data_evento.date() < hoje.date()
        hoje_flag = data_evento.date() == hoje.date()
        semana.append({
            "dia":     dias_pt[d],
            "data":    data_evento.strftime("%d/%m"),
            "hora":    hora,
            "pais":    pais,
            "evento":  evento,
            "impacto": impacto,
            "passou":  passou,
            "hoje":    hoje_flag,
        })
    return semana

# ══════════════════════════════════════════════
# ANÁLISE NARRATIVA
# ══════════════════════════════════════════════
def gerar_analise_narrativa(data):
    q = data
    def chg(k): return (q.get(k) or {}).get("change") or 0
    def prc(k): return (q.get(k) or {}).get("price")

    ibov=chg("ibovespa"); dolar=chg("dolar"); sp=chg("sp500")
    btc=chg("bitcoin"); brent=chg("brent"); ouro=chg("ouro")
    petr4=chg("petr4"); vale3=chg("vale3")

    paragrafos = []

    # Parágrafo 1 — Panorama geral
    if ibov > 1:
        pan = f"O mercado brasileiro operou em alta nesta sessão, com o Ibovespa registrando valorização de {fc(ibov)}, refletindo otimismo dos investidores e fluxo positivo de capital."
    elif ibov < -1:
        pan = f"O mercado brasileiro apresentou queda expressiva nesta sessão, com o Ibovespa recuando {fc(ibov)}, em meio a pressões vendedoras e aversão ao risco."
    elif ibov > 0:
        pan = f"O Ibovespa encerrou o dia com leve alta de {fc(ibov)}, em sessão de baixa volatilidade e sem catalisadores relevantes."
    else:
        pan = f"O Ibovespa operou em queda moderada de {fc(ibov)}, em sessão marcada por realização de lucros."
    paragrafos.append(pan)

    # Parágrafo 2 — Dólar e inflação
    if dolar > 1:
        par2 = f"O dólar subiu {fc(dolar)}, pressionado por fatores externos e incertezas fiscais domésticas. A alta da moeda americana tende a pressionar a inflação via importados e combustíveis, o que pode aumentar a pressão sobre o Banco Central na próxima reunião do Copom."
    elif dolar < -1:
        par2 = f"O dólar recuou {fc(dolar)}, trazendo alívio ao cenário inflacionário. A queda da moeda americana favorece importadores e contribui para a ancoragem das expectativas de inflação."
    else:
        par2 = f"O dólar oscilou levemente ({fc(dolar)}), sem grandes impactos no cenário inflacionário. A taxa de câmbio se mantém em patamar controlado, compatível com a meta de inflação vigente."
    paragrafos.append(par2)

    # Parágrafo 3 — Wall Street e cenário internacional
    if sp > 0.8:
        par3 = f"Wall Street operou em alta, com o S&P 500 subindo {fc(sp)}. O ambiente externo favorável tende a beneficiar mercados emergentes como o Brasil, atraindo fluxo de capital estrangeiro para a bolsa."
    elif sp < -0.8:
        par3 = f"Wall Street fechou em queda, com o S&P 500 recuando {fc(sp)}, sinalizando cautela dos investidores globais. O cenário externo desfavorável contribuiu para o aumento da aversão ao risco nos mercados emergentes."
    else:
        par3 = f"Wall Street operou de forma estável ({fc(sp)}), sem grandes impactos sobre os mercados globais. O cenário externo permanece neutro para os ativos brasileiros."
    paragrafos.append(par3)

    # Parágrafo 4 — Petróleo e Petrobras
    if abs(brent) > 1.5:
        if brent < 0:
            par4 = f"O petróleo Brent recuou {fc(brent)}, impactando negativamente as ações da Petrobras (PETR4: {fc(petr4)}). A queda do barril pode pressionar as receitas da estatal no curto prazo, embora alivie os preços dos combustíveis ao consumidor."
        else:
            par4 = f"O petróleo Brent subiu {fc(brent)}, beneficiando as ações da Petrobras (PETR4: {fc(petr4)}). A alta do barril favorece as receitas da estatal, mas pode pressionar os preços dos combustíveis nas próximas semanas."
    else:
        par4 = f"O petróleo Brent operou estável ({fc(brent)}), com a Petrobras (PETR4) registrando variação de {fc(petr4)} no dia. O impacto sobre os combustíveis deve ser neutro no curto prazo."
    paragrafos.append(par4)

    # Parágrafo 5 — Bitcoin e ouro
    extras = []
    if abs(btc) > 2:
        if btc > 0:
            extras.append(f"O Bitcoin registrou forte alta de {fc(btc)}, sinalizando apetite por ativos de risco e possível entrada de capital institucional no mercado cripto.")
        else:
            extras.append(f"O Bitcoin caiu {fc(btc)}, em movimento de correção. Investidores em cripto devem manter cautela e evitar decisões baseadas em volatilidade de curto prazo.")
    if ouro > 1.5:
        extras.append(f"O ouro subiu {fc(ouro)}, indicando busca por proteção (safe haven) e possível aumento da incerteza global.")
    elif ouro < -1.5:
        extras.append(f"O ouro recuou {fc(ouro)}, sugerindo maior confiança dos investidores e redução da aversão ao risco.")
    if extras:
        paragrafos.append(" ".join(extras))

    # Conclusão
    positivos = sum([ibov>0, sp>0, btc>0, dolar<0])
    if positivos >= 3:
        conclusao = "Em síntese, o dia foi majoritariamente positivo para os mercados. O ambiente favorece posições em renda variável, embora a prudência siga recomendada dado o cenário de juros ainda elevados no Brasil."
    elif positivos <= 1:
        conclusao = "Em síntese, o dia foi de cautela nos mercados. Em momentos de aversão ao risco, renda fixa de qualidade e diversificação da carteira seguem sendo as melhores estratégias."
    else:
        conclusao = "Em síntese, o dia foi misto para os mercados. A falta de tendência clara reforça a importância de manter uma carteira diversificada e alinhada ao perfil de risco de cada investidor."
    paragrafos.append(conclusao)

    return paragrafos

# ══════════════════════════════════════════════
# TEXTOS TELEGRAM
# ══════════════════════════════════════════════
def market_text(data, titulo="📊 Mercado"):
    q = data
    def a(k): return arrow((q.get(k) or {}).get("change"))
    def v(k,dec=2,pfx=""): return fmt((q.get(k) or {}).get("price"),dec,pfx)
    def c(k): return fc((q.get(k) or {}).get("change"))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    return (
        f"{titulo} — *{now}*\n\n"
        f"*🇧🇷 Brasil*\n"
        f"{a('ibovespa')} Ibovespa: `{v('ibovespa',0)}` {c('ibovespa')}\n"
        f"{a('dolar')} Dólar: `R$ {v('dolar',4)}` {c('dolar')}\n"
        f"{a('petr4')} PETR4: `R$ {v('petr4')}` {c('petr4')}\n"
        f"{a('vale3')} VALE3: `R$ {v('vale3')}` {c('vale3')}\n"
        f"{a('itub4')} ITUB4: `R$ {v('itub4')}` {c('itub4')}\n\n"
        f"*🌍 Internacional*\n"
        f"{a('sp500')} S&P 500: `{v('sp500',0)}` {c('sp500')}\n"
        f"{a('nasdaq')} Nasdaq: `{v('nasdaq',0)}` {c('nasdaq')}\n"
        f"{a('brent')} Brent: `US$ {v('brent')}` {c('brent')}\n"
        f"{a('ouro')} Ouro: `US$ {v('ouro',0)}` {c('ouro')}\n"
        f"{a('bitcoin')} Bitcoin: `US$ {v('bitcoin',0)}` {c('bitcoin')}\n\n"
        f"💰 Selic `{data['selic']}%` | CDI `{data['cdi']}%`\n\n"
        f"_Atualizado às {data['updated_at']}_"
    )

def analise_rapida(data):
    q = data
    def chg(k): return (q.get(k) or {}).get("change") or 0
    ibov=chg("ibovespa"); dolar=chg("dolar"); sp=chg("sp500")
    btc=chg("bitcoin"); brent=chg("brent"); ouro=chg("ouro")
    linhas=[f"🤖 *Análise — {datetime.now().strftime('%d/%m/%Y %H:%M')}*\n"]
    if ibov>1.5:    linhas.append(f"📈 *Ibovespa* alta forte ({fc(ibov)}) — apetite a risco elevado.")
    elif ibov>0.3:  linhas.append(f"📈 *Ibovespa* alta moderada ({fc(ibov)}) — mercado com bom humor.")
    elif ibov<-1.5: linhas.append(f"📉 *Ibovespa* queda forte ({fc(ibov)}) — cautela. Possível oportunidade no longo prazo.")
    elif ibov<-0.3: linhas.append(f"📉 *Ibovespa* leve queda ({fc(ibov)}) — realização de lucros.")
    else:           linhas.append(f"➡️ *Ibovespa* estável ({fc(ibov)}) — sem direção clara.")
    if dolar>1:     linhas.append(f"💵 *Dólar* subindo ({fc(dolar)}) — pressão sobre inflação.")
    elif dolar<-1:  linhas.append(f"💵 *Dólar* caindo ({fc(dolar)}) — alívio inflacionário.")
    if sp>0.8:      linhas.append(f"🇺🇸 *Wall Street* alta ({fc(sp)}) — ambiente global favorável.")
    elif sp<-0.8:   linhas.append(f"🇺🇸 *Wall Street* queda ({fc(sp)}) — risco global aumentando.")
    if btc>4:       linhas.append(f"₿ *Bitcoin* disparando ({fc(btc)}) — euforia no cripto.")
    elif btc<-4:    linhas.append(f"₿ *Bitcoin* desabando ({fc(btc)}) — volatilidade extrema.")
    elif abs(btc)>2: linhas.append(f"₿ *Bitcoin* {fc(btc)} — movimento relevante no cripto.")
    if brent<-2:    linhas.append(f"🛢 *Brent* queda forte ({fc(brent)}) — possível alívio nos combustíveis.")
    elif brent>2:   linhas.append(f"🛢 *Brent* alta ({fc(brent)}) — pressão sobre combustíveis.")
    if ouro>1.5:    linhas.append(f"🥇 *Ouro* alta ({fc(ouro)}) — busca por proteção. Incerteza no ar.")
    positivos=sum([ibov>0,sp>0,btc>0,ouro>0])
    if positivos>=3:   linhas.append(f"\n✅ *Cenário: POSITIVO*")
    elif positivos<=1: linhas.append(f"\n⚠️ *Cenário: NEGATIVO* — cautela.")
    else:              linhas.append(f"\n🔄 *Cenário: MISTO*")
    linhas.append("_Análise automática FinVest_")
    return "\n".join(linhas)

def briefing_text(data):
    import random
    now = datetime.now()
    dia = now.strftime("%A, %d/%m/%Y").capitalize()
    q = data
    def chg(k): return (q.get(k) or {}).get("change") or 0
    def v(k,dec=2,pfx=""): return fmt((q.get(k) or {}).get("price"),dec,pfx)
    ideias=["Comparativo: poupança vs Tesouro Selic em 10 anos",
            "O que é CDI? Explicação simples","Quanto rende R$500/mês por 10 anos",
            "5 erros de quem começa a investir","Renda fixa vs renda variável",
            "Resumo semanal do mercado","Como a Selic afeta seus investimentos",
            "Tesouro Direto: mito vs realidade","Por que diversificar?",
            "Quanto você perde deixando na poupança"]
    return (
        f"☀️ *Briefing FinVest — {dia}*\n\n"
        f"*📊 Mercado*\n"
        f"Ibovespa: `{v('ibovespa',0)}` ({fc(chg('ibovespa'))})\n"
        f"Dólar: `R$ {v('dolar',4)}` ({fc(chg('dolar'))})\n"
        f"S&P 500: `{v('sp500',0)}` ({fc(chg('sp500'))})\n"
        f"Bitcoin: `US$ {v('bitcoin',0)}` ({fc(chg('bitcoin'))})\n\n"
        f"*💡 Sugestão de post*\n_{random.choice(ideias)}_\n\n"
        f"*✅ Checklist*\n"
        f"⬜ Verificar abertura\n⬜ Postar conteúdo\n"
        f"⬜ Responder DMs\n⬜ PDF às 18h\n\nBom dia! 🚀"
    )

# ══════════════════════════════════════════════
# PDF COMPLETO COM ANÁLISE + NOTÍCIAS + CALENDÁRIO
# ══════════════════════════════════════════════
def gerar_pdf_bytes(data, news=None, calendario=None):
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import simpleSplit

    DARK =HexColor('#0D1117'); DARK2=HexColor('#161B22'); CARD=HexColor('#1C2128')
    ACCENT=HexColor('#00C851'); RED=HexColor('#FF4444'); GOLD=HexColor('#FFD700')
    WHITE=HexColor('#FFFFFF'); GRAY=HexColor('#8B949E'); TEXT2=HexColor('#E6EDF3')
    ROW1=HexColor('#1A1F26'); SUBTEXT=HexColor('#C9D1D9'); ORANGE=HexColor('#F0A500')

    W,H=A4; MM=2.8346
    def p(v): return v*MM

    buf=io.BytesIO()
    c=rl_canvas.Canvas(buf,pagesize=A4)

    def bg(c):
        c.setFillColor(DARK); c.rect(0,0,W,H,fill=1,stroke=0)

    def header(c):
        c.setFillColor(DARK2); c.rect(0,H-p(22),W,p(22),fill=1,stroke=0)
        c.setFillColor(ACCENT); c.rect(0,H-p(22),W,1.5,fill=1,stroke=0)
        c.setFont('Helvetica-Bold',20); c.setFillColor(WHITE)
        c.drawString(p(14),H-p(13),'FinVest')
        c.setFont('Helvetica-Bold',7); c.setFillColor(ACCENT)
        c.drawString(p(14),H-p(19),'JORNAL FINANCEIRO DIARIO')
        c.setFont('Helvetica',8); c.setFillColor(GRAY)
        c.drawRightString(W-p(14),H-p(13),datetime.now().strftime("%A, %d/%m/%Y"))
        c.drawRightString(W-p(14),H-p(19),f'Gerado as {datetime.now().strftime("%H:%M")} | @finvesti.br')

    def footer(c,pg,total):
        c.setFillColor(DARK2); c.rect(0,0,W,p(12),fill=1,stroke=0)
        c.setFillColor(ACCENT); c.rect(0,p(12),W,1,fill=1,stroke=0)
        c.setFont('Helvetica',7); c.setFillColor(GRAY)
        c.drawString(p(14),p(4),'FinVest | @finvesti.br | finvest.com.br')
        c.drawCentredString(W/2,p(4),'Conteudo informativo. Nao constitui recomendacao de investimento.')
        c.drawRightString(W-p(14),p(4),f'Pag. {pg} / {total}')

    def sec(c,y,txt,color=None):
        col = color or CARD
        c.setFillColor(col); c.rect(p(14),y-p(8),W-p(28),p(8),fill=1,stroke=0)
        c.setFillColor(ACCENT); c.rect(p(14),y-p(8),3,p(8),fill=1,stroke=0)
        c.setFont('Helvetica-Bold',8); c.setFillColor(GRAY)
        c.drawString(p(18),y-p(5.2),txt.upper())
        return y-p(10)

    def wrap_text(c, text, x, y, max_width, font, size, color):
        c.setFont(font,size); c.setFillColor(color)
        lines = simpleSplit(text, font, size, max_width)
        for line in lines:
            if y < p(18): return y
            c.drawString(x, y, line)
            y -= size*0.45*MM + p(0.8)
        return y

    cols=[p(52),p(22),p(36),p(42)]; total_w=sum(cols)

    def tbl_hdr(c,y):
        c.setFillColor(CARD); c.rect(p(14),y-p(7),total_w,p(7),fill=1,stroke=0)
        cx=p(14)
        for h,w in zip(["Ativo","Var %","Preco","Min / Max"],cols):
            c.setFont('Helvetica-Bold',7); c.setFillColor(GRAY)
            c.drawString(cx+p(2),y-p(4.8),h); cx+=w
        return y-p(7)

    def tbl_row(c,y,cells,chg,i):
        c.setFillColor(ROW1 if i%2==0 else DARK2)
        c.rect(p(14),y-p(6.5),total_w,p(6.5),fill=1,stroke=0)
        cx=p(14)
        for j,(v,w) in enumerate(zip(cells,cols)):
            if j==1: c.setFont('Helvetica-Bold',8); c.setFillColor(ACCENT if (chg or 0)>=0 else RED)
            else: c.setFont('Helvetica',8); c.setFillColor(TEXT2)
            c.drawString(cx+p(2),y-p(4.3),str(v)); cx+=w
        return y-p(6.5)

    # ═══════════════════════
    # PÁGINA 1 — Cotações
    # ═══════════════════════
    bg(c); header(c); y=H-p(26)

    kpis=[("IBOVESPA","ibovespa","",0),("DOLAR","dolar","R$ ",4),("S&P 500","sp500","",0),
          ("NASDAQ","nasdaq","",0),("BRENT","brent","US$ ",2),("BITCOIN","bitcoin","US$ ",0)]
    cw=(W-p(28)-p(8))/3; ch=p(26); gap=p(4)
    for i,(lbl,key,pfx,dec) in enumerate(kpis):
        d=data.get(key,{}); col=i%3; row=i//3
        cx=p(14)+col*(cw+gap); cy=y-row*(ch+gap)
        chg=d.get("change") or 0; cc=ACCENT if chg>=0 else RED
        c.setFillColor(DARK2); c.roundRect(cx,cy-ch,cw,ch,3,fill=1,stroke=0)
        c.setFillColor(cc); c.rect(cx,cy-p(1.5),cw,p(1.5),fill=1,stroke=0)
        c.setFont('Helvetica',7); c.setFillColor(GRAY); c.drawString(cx+p(3),cy-p(7),lbl)
        price=d.get("price")
        c.setFont('Helvetica-Bold',13); c.setFillColor(WHITE)
        c.drawString(cx+p(3),cy-p(16),fmt(price,dec,pfx) if price else "—")
        bw=p(22); c.setFillColor(cc); c.roundRect(cx+p(3),cy-p(23),bw,p(5),2,fill=1,stroke=0)
        c.setFont('Helvetica-Bold',7.5); c.setFillColor(HexColor('#0D1117'))
        c.drawCentredString(cx+p(3)+bw/2,cy-p(20),fc(chg))
    y-=2*(ch+gap)+p(6)

    y=sec(c,y,"Brasil — Acoes"); y=tbl_hdr(c,y)
    for i,(lbl,key) in enumerate([("Petrobras (PETR4)","petr4"),("Vale (VALE3)","vale3"),
          ("Itau (ITUB4)","itub4"),("Bradesco (BBDC4)","bbdc4"),
          ("Ambev (ABEV3)","abev3"),("WEG (WEGE3)","wege3")]):
        d=data.get(key,{}); chg=d.get("change") or 0
        y=tbl_row(c,y,[lbl,fc(chg),fmt(d.get("price"),2,"R$ "),
            f"R$ {fmt(d.get('low'),2)} / R$ {fmt(d.get('high'),2)}"],chg,i)
    y-=p(4)

    y=sec(c,y,"Mercados Internacionais"); y=tbl_hdr(c,y)
    for i,(lbl,key,pfx,dec) in enumerate([("S&P 500","sp500","",0),("Dow Jones","dow","",0),
          ("Nasdaq","nasdaq","",0),("Petroleo Brent","brent","US$ ",2),
          ("Ouro","ouro","US$ ",0),("Bitcoin","bitcoin","US$ ",0)]):
        d=data.get(key,{}); chg=d.get("change") or 0
        y=tbl_row(c,y,[lbl,fc(chg),fmt(d.get("price"),dec,pfx),
            f"{pfx}{fmt(d.get('low'),dec)} / {pfx}{fmt(d.get('high'),dec)}"],chg,i)
    y-=p(4)

    # Taxas
    tw=(W-p(28)-p(4))/2
    c.setFillColor(DARK2); c.roundRect(p(14),y-p(14),tw,p(14),3,fill=1,stroke=0)
    c.setFont('Helvetica',8); c.setFillColor(GRAY); c.drawString(p(18),y-p(5),"Selic ao ano")
    c.setFont('Helvetica-Bold',14); c.setFillColor(GOLD); c.drawString(p(18),y-p(12),f"{data.get('selic','—')}%")
    c.setFillColor(DARK2); c.roundRect(p(14)+tw+p(4),y-p(14),tw,p(14),3,fill=1,stroke=0)
    c.setFont('Helvetica',8); c.setFillColor(GRAY); c.drawString(p(14)+tw+p(8),y-p(5),"CDI ao ano")
    c.setFont('Helvetica-Bold',14); c.setFillColor(GOLD); c.drawString(p(14)+tw+p(8),y-p(12),f"{data.get('cdi','—')}%")

    footer(c,1,3); c.showPage()

    # ═══════════════════════
    # PÁGINA 2 — Análise + Notícias
    # ═══════════════════════
    bg(c); header(c); y=H-p(26)

    # Análise narrativa
    y=sec(c,y,"Analise do Dia")
    y-=p(2)
    paragrafos = gerar_analise_narrativa(data)
    max_w = W - p(28)
    for para in paragrafos:
        if y < p(30): break
        y = wrap_text(c, para, p(14), y, max_w, 'Helvetica', 8.5, SUBTEXT)
        y -= p(3)

    y -= p(4)

    # Notícias
    if news:
        y = sec(c, y, "Ultimas Noticias de Mercado")
        y -= p(2)
        br_news  = [n for n in news if any(w in n["title"].lower() for w in ["brasil","ibovespa","b3","bovespa","petrobras","vale","real","selic","copom","ipca"])]
        int_news = [n for n in news if n not in br_news]

        # BR
        if br_news:
            c.setFont('Helvetica-Bold',7.5); c.setFillColor(ACCENT)
            c.drawString(p(14), y, "BRASIL"); y -= p(5)
            for i,n in enumerate(br_news[:4]):
                if y < p(30): break
                rh = p(13)
                c.setFillColor(ROW1 if i%2==0 else DARK2)
                c.rect(p(14),y-rh,W-p(28),rh,fill=1,stroke=0)
                c.setFillColor(ACCENT); c.rect(p(14),y-rh,2,rh,fill=1,stroke=0)
                meta = n.get("date","")
                if n.get("source"): meta += f"  |  {n['source']}"
                c.setFont('Helvetica',6.5); c.setFillColor(GRAY)
                c.drawString(p(18),y-p(3.5),meta)
                c.setFont('Helvetica-Bold',8); c.setFillColor(WHITE)
                title = n.get("title","")[:88]
                c.drawString(p(18),y-p(9),title)
                y -= rh

        y -= p(3)

        # Internacional
        if int_news:
            c.setFont('Helvetica-Bold',7.5); c.setFillColor(ACCENT)
            c.drawString(p(14), y, "INTERNACIONAL"); y -= p(5)
            for i,n in enumerate(int_news[:4]):
                if y < p(30): break
                rh = p(13)
                c.setFillColor(ROW1 if i%2==0 else DARK2)
                c.rect(p(14),y-rh,W-p(28),rh,fill=1,stroke=0)
                c.setFillColor(HexColor('#3498DB')); c.rect(p(14),y-rh,2,rh,fill=1,stroke=0)
                meta = n.get("date","")
                if n.get("source"): meta += f"  |  {n['source']}"
                c.setFont('Helvetica',6.5); c.setFillColor(GRAY)
                c.drawString(p(18),y-p(3.5),meta)
                c.setFont('Helvetica-Bold',8); c.setFillColor(WHITE)
                title = n.get("title","")[:88]
                c.drawString(p(18),y-p(9),title)
                y -= rh

    footer(c,2,3); c.showPage()

    # ═══════════════════════
    # PÁGINA 3 — Calendário + Glossário
    # ═══════════════════════
    bg(c); header(c); y=H-p(26)

    # Calendário econômico
    y=sec(c,y,"Calendario Economico da Semana")
    y-=p(2)

    cal = calendario or get_calendario()

    # Header calendário
    cal_cols=[p(20),p(14),p(16),p(16),p(60),p(22)]
    cal_hdrs=["Dia","Data","Hora","País","Evento","Impacto"]
    c.setFillColor(CARD); c.rect(p(14),y-p(7),sum(cal_cols),p(7),fill=1,stroke=0)
    cx=p(14)
    for h,w in zip(cal_hdrs,cal_cols):
        c.setFont('Helvetica-Bold',7); c.setFillColor(GRAY)
        c.drawString(cx+p(1.5),y-p(4.8),h); cx+=w
    y-=p(7)

    for i,ev in enumerate(cal):
        rh=p(7)
        if y-rh < p(18): break
        # Cor de fundo
        if ev.get("hoje"):   bg_col=HexColor('#1a2a1a')
        elif ev.get("passou"): bg_col=HexColor('#111518')
        else:                bg_col=ROW1 if i%2==0 else DARK2
        c.setFillColor(bg_col); c.rect(p(14),y-rh,sum(cal_cols),rh,fill=1,stroke=0)

        # Indicador impacto
        imp_col = RED if ev["impacto"]=="alto" else ORANGE if ev["impacto"]=="médio" else GRAY
        c.setFillColor(imp_col); c.rect(p(14),y-rh,2,rh,fill=1,stroke=0)

        # Indicador hoje
        if ev.get("hoje"):
            c.setFillColor(ACCENT); c.rect(p(14)+sum(cal_cols)-2,y-rh,2,rh,fill=1,stroke=0)

        cells=[ev["dia"],ev["data"],ev["hora"],ev["pais"],ev["evento"],ev["impacto"].upper()]
        cx=p(14)
        for j,(v,w) in enumerate(zip(cells,cal_cols)):
            passou = ev.get("passou",False)
            if j==5:
                imp_c = RED if v=="ALTO" else ORANGE if v=="MÉDIO" else GRAY
                c.setFont('Helvetica-Bold',7); c.setFillColor(imp_c)
            elif passou:
                c.setFont('Helvetica',7.5); c.setFillColor(GRAY)
            elif ev.get("hoje"):
                c.setFont('Helvetica-Bold',7.5); c.setFillColor(WHITE)
            else:
                c.setFont('Helvetica',7.5); c.setFillColor(TEXT2)
            c.drawString(cx+p(1.5),y-p(4.5),str(v)); cx+=w
        y-=rh

    # Legenda impacto
    y-=p(3)
    c.setFont('Helvetica',7); c.setFillColor(GRAY)
    c.drawString(p(14),y,"Impacto:")
    for imp,col,offset in [("Alto",RED,p(30)),("Médio",ORANGE,p(46)),("Baixo",GRAY,p(62))]:
        c.setFillColor(col); c.rect(p(14)+offset,y-p(1),p(3),p(3),fill=1,stroke=0)
        c.setFillColor(GRAY); c.drawString(p(14)+offset+p(4),y,imp)
    y-=p(8)

    # Glossário
    y=sec(c,y,"Glossario dos Indicadores")
    gloss=[
        ("Ibovespa","Principal indice da B3. Mede o desempenho medio das acoes mais negociadas."),
        ("Selic","Taxa basica de juros do Brasil, definida pelo Banco Central."),
        ("CDI","Taxa do mercado interbancario. Referencia para renda fixa."),
        ("Brent","Referencia global do preco do petroleo, em dolares por barril."),
        ("Dolar","Cotacao do USD em reais. Afeta importados, combustiveis e inflacao."),
        ("S&P 500","Indice das 500 maiores empresas americanas. Termometro global."),
        ("Bitcoin","Principal criptomoeda global. Alta volatilidade."),
        ("Var %","Variacao percentual em relacao ao fechamento anterior."),
    ]
    for i,(term,defn) in enumerate(gloss):
        if y-p(6.5)<p(18): break
        c.setFillColor(ROW1 if i%2==0 else DARK2)
        c.rect(p(14),y-p(6.5),W-p(28),p(6.5),fill=1,stroke=0)
        c.setFont('Helvetica-Bold',8); c.setFillColor(ACCENT)
        c.drawString(p(17),y-p(4.3),term)
        c.setFont('Helvetica',8); c.setFillColor(TEXT2)
        c.drawString(p(50),y-p(4.3),defn)
        y-=p(6.5)

    y-=p(6)
    if y>p(30):
        c.setFillColor(ACCENT); c.roundRect(p(14),y-p(18),W-p(28),p(18),5,fill=1,stroke=0)
        c.setFont('Helvetica-Bold',10); c.setFillColor(HexColor('#0D1117'))
        c.drawCentredString(W/2,y-p(9),'"O melhor investimento e em educacao financeira."')
        c.setFont('Helvetica-Bold',8)
        c.drawCentredString(W/2,y-p(15),'FinVest  |  @finvesti.br  |  finvest.com.br')

    footer(c,3,3); c.save(); buf.seek(0)
    return buf

# ══════════════════════════════════════════════
# JOBS AUTOMÁTICOS
# ══════════════════════════════════════════════
async def job_briefing(ctx):
    config=load_json(CONFIG_FILE,{}); chat_id=config.get("chat_id")
    if not chat_id: return
    data=await fetch_all_fast()
    await ctx.bot.send_message(chat_id=chat_id,text=briefing_text(data),parse_mode="Markdown")

async def job_pdf_fechamento(ctx):
    config=load_json(CONFIG_FILE,{}); chat_id=config.get("chat_id")
    if not chat_id: return
    data,news=await asyncio.gather(fetch_all_fast(),fetch_news_async())
    pdf=gerar_pdf_bytes(data,news)
    filename=f"FinVest_Fechamento_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    await ctx.bot.send_document(chat_id=chat_id,document=pdf,filename=filename,
        caption=f"📄 *Fechamento — {datetime.now().strftime('%d/%m/%Y')}*\n_Com análise, notícias e calendário econômico_",
        parse_mode="Markdown")

async def job_alertas(ctx):
    alertas=load_json(ALERTAS_FILE,[])
    if not alertas: return
    config=load_json(CONFIG_FILE,{}); chat_id=config.get("chat_id")
    if not chat_id: return
    keys_needed=list(set(a["key"] for a in alertas))
    tasks=[get_quote_async(k,TICKERS[k]) for k in keys_needed if k in TICKERS]
    results=dict(await asyncio.gather(*tasks))
    disparados=[]
    for alerta in alertas:
        d=results.get(alerta["key"],{}); price=d.get("price")
        if price is None: continue
        if alerta["tipo"]=="acima" and price>=alerta["valor"]: disparados.append((alerta,price))
        elif alerta["tipo"]=="abaixo" and price<=alerta["valor"]: disparados.append((alerta,price))
    for alerta,price in disparados:
        emoji="🚀" if alerta["tipo"]=="acima" else "⚠️"
        await ctx.bot.send_message(chat_id=chat_id,parse_mode="Markdown",
            text=f"{emoji} *ALERTA!*\n*{alerta['key'].upper()}* atingiu `{fmt(price,2)}`\n"
                 f"Alerta: {alerta['tipo']} de `{fmt(alerta['valor'],2)}`")
    if disparados:
        ids=[a["id"] for a,_ in disparados]
        save_json(ALERTAS_FILE,[a for a in alertas if a["id"] not in ids])

# ══════════════════════════════════════════════
# TECLADOS
# ══════════════════════════════════════════════
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Mercado"),      KeyboardButton("🤖 Análise")],
        [KeyboardButton("📄 PDF Agora"),    KeyboardButton("📱 Gerar Post")],
        [KeyboardButton("🔔 Alertas"),      KeyboardButton("💰 Simular")],
        [KeyboardButton("✅ Tarefas"),      KeyboardButton("➕ Tarefa")],
        [KeyboardButton("⚙️ Configurar"),  KeyboardButton("❓ Ajuda")],
    ],resize_keyboard=True)

def post_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 Comparativo"),  KeyboardButton("📈 Fechamento")],
        [KeyboardButton("💡 Dica"),         KeyboardButton("📌 Selic")],
        [KeyboardButton("🔙 Voltar")],
    ],resize_keyboard=True)

def config_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⏰ Horário Briefing"), KeyboardButton("📄 Horário PDF")],
        [KeyboardButton("🔙 Voltar")],
    ],resize_keyboard=True)

# ══════════════════════════════════════════════
# POSTS
# ══════════════════════════════════════════════
POSTS={
    "comparativo":lambda d:"📊 *COMPARATIVO DE INVESTIMENTOS*\n\nQuanto rende R$10 mil em 1 ano?\n\n🇧🇷 Tesouro Selic → R$ 10.925,94\n🏦 CDB 100% CDI → R$ 10.904,64\n♻️ LCI/LCA 90% CDI → R$ 10.457,58\n🏛 Poupança → R$ 10.166,77\n📈 Ibovespa → R$ 11.008,18\n\nEm 10 anos a diferença muda TUDO. 👀\n\nSalva! 📌\n⚠️ Não constitui recomendação.\n\n#educacaofinanceira #investimentos #finvest",
    "fechamento":lambda d:(
        f"📊 *FECHAMENTO — {datetime.now().strftime('%d/%m/%Y')}*\n\n"
        f"🇧🇷 Ibovespa: {fmt((d.get('ibovespa') or {}).get('price'),0)} ({fc((d.get('ibovespa') or {}).get('change'))})\n"
        f"💵 Dólar: R$ {fmt((d.get('dolar') or {}).get('price'),4)} ({fc((d.get('dolar') or {}).get('change'))})\n"
        f"🇺🇸 S&P 500: {fmt((d.get('sp500') or {}).get('price'),0)} ({fc((d.get('sp500') or {}).get('change'))})\n"
        f"₿ Bitcoin: US$ {fmt((d.get('bitcoin') or {}).get('price'),0)} ({fc((d.get('bitcoin') or {}).get('change'))})\n\n"
        "Acompanhe aqui todo dia! 👇\n\n⚠️ Conteúdo informativo.\n\n#mercadofinanceiro #ibovespa #finvest"
    ),
    "dica":lambda d:"💡 *DICA DO DIA*\n\nR$100 por mês durante 10 anos = mais de R$20 mil\n\nO segredo? Juros compostos + consistência. 🔑\n\nNão precisa de muito. Precisa começar. 🚀\n\nSalva e compartilha! 📌\n\n#educacaofinanceira #finvest #investimentos",
    "selic":lambda d:(
        f"📌 *SELIC: {TAXAS['selic']}% ao ano*\n\n"
        f"✅ Tesouro Selic: ~{TAXAS['selic']}% a.a.\n✅ CDB 100% CDI: ~{TAXAS['cdi']}% a.a.\n❌ Poupança: bem menos\n\n"
        "Ainda na poupança? Esse post é pra você. 👆\n\n⚠️ Não constitui recomendação.\n\n#selic #tesourodireto #finvest"
    ),
}

def simular(valor,anos,taxa):
    r=valor*((1+taxa/100)**anos); return r, r-valor

# ══════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════
async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    config=load_json(CONFIG_FILE,{})
    config["chat_id"]=update.effective_chat.id
    save_json(CONFIG_FILE,config)
    jq=ctx.job_queue
    for name,fn,h,m in [("briefing",job_briefing,8,0),("pdf_fechamento",job_pdf_fechamento,18,0)]:
        for j in jq.get_jobs_by_name(name): j.schedule_removal()
        jq.run_daily(fn,time=dtime(hour=h,minute=m),name=name)
    for j in jq.get_jobs_by_name("alertas"): j.schedule_removal()
    jq.run_repeating(job_alertas,interval=1800,name="alertas")
    nome=update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Olá, *{nome}*! FinVest Agent v5 ativo.\n\n"
        f"☀️ Briefing: *08:00* | 📄 PDF: *18:00* | 🔔 Alertas: *30min*\n\n"
        f"📄 O PDF agora inclui:\n"
        f"• Análise detalhada do dia\n• Notícias BR e Internacional\n• Calendário econômico da semana\n\n"
        f"Use ⚙️ Configurar para ajustar horários.",
        parse_mode="Markdown",reply_markup=main_keyboard())

async def handle_message(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    text=update.message.text
    await update.message.reply_chat_action("typing")

    if text=="📊 Mercado":
        await update.message.reply_text("⏳ Buscando cotações...",reply_markup=main_keyboard())
        data=await fetch_all_fast()
        await update.message.reply_text(market_text(data),parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="🤖 Análise":
        await update.message.reply_text("⏳ Analisando mercado...",reply_markup=main_keyboard())
        data=await fetch_all_fast()
        await update.message.reply_text(analise_rapida(data),parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="📄 PDF Agora":
        await update.message.reply_text("⏳ Gerando PDF completo com análise, notícias e calendário...",reply_markup=main_keyboard())
        try:
            data,news=await asyncio.gather(fetch_all_fast(),fetch_news_async())
            pdf=gerar_pdf_bytes(data,news)
            filename=f"FinVest_{datetime.now().strftime('%Y-%m-%d_%H%M')}.pdf"
            await update.message.reply_document(document=pdf,filename=filename,
                caption=f"📄 *Relatório FinVest — {datetime.now().strftime('%d/%m/%Y %H:%M')}*\n\n"
                        f"✅ Cotações atualizadas\n✅ Análise do dia\n✅ Notícias BR e Internacional\n✅ Calendário econômico",
                parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Erro: {e}",reply_markup=main_keyboard())

    elif text=="📱 Gerar Post":
        await update.message.reply_text("Escolha o tipo:",reply_markup=post_keyboard())

    elif text in["📊 Comparativo","📈 Fechamento","💡 Dica","📌 Selic"]:
        tipo_map={"📊 Comparativo":"comparativo","📈 Fechamento":"fechamento","💡 Dica":"dica","📌 Selic":"selic"}
        data=await fetch_all_fast()
        post=POSTS[tipo_map[text]](data)
        await update.message.reply_text(f"{post}\n\n_Copie e cole no Instagram! ✂️_",
            parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="🔙 Voltar":
        await update.message.reply_text("Menu 👇",reply_markup=main_keyboard())

    elif text=="🔔 Alertas":
        alertas=load_json(ALERTAS_FILE,[])
        if not alertas:
            msg="🔔 *Nenhum alerta ativo.*\n\nPara criar:\n`alerta PETR4 acima 45`\n`alerta BITCOIN abaixo 70000`\n`alerta DOLAR acima 5.50`"
        else:
            msg="🔔 *Alertas ativos:*\n\n"
            for i,a in enumerate(alertas,1):
                msg+=f"{i}. *{a['key'].upper()}* {a['tipo']} `{fmt(a['valor'],2)}`\n"
            msg+="\nPara remover: `remover 1`"
        await update.message.reply_text(msg,parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="💰 Simular":
        ctx.user_data["aguardando"]="simular"
        await update.message.reply_text("💰 Digite: `valor anos taxa`\nEx: `10000 5 14.75`",
            parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="✅ Tarefas":
        tarefas=load_json(TAREFAS_FILE,[])
        if not tarefas:
            await update.message.reply_text("📋 Nenhuma tarefa. Use *➕ Tarefa*!",parse_mode="Markdown",reply_markup=main_keyboard())
        else:
            msg="📋 *Tarefas:*\n\n"
            for i,t in enumerate(tarefas,1):
                msg+=f"{'✅' if t.get('done') else '⬜'} {i}. {t['texto']}\n"
            msg+="\nResponda com o número para marcar/desmarcar."
            ctx.user_data["aguardando"]="check_tarefa"
            await update.message.reply_text(msg,parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="➕ Tarefa":
        ctx.user_data["aguardando"]="nova_tarefa"
        await update.message.reply_text("📝 Digite a tarefa:",reply_markup=main_keyboard())

    elif text=="⚙️ Configurar":
        config=load_json(CONFIG_FILE,{})
        await update.message.reply_text(
            f"⚙️ *Configurações*\n\n☀️ Briefing: `{config.get('h_briefing','08:00')}`\n📄 PDF: `{config.get('h_pdf','18:00')}`",
            parse_mode="Markdown",reply_markup=config_keyboard())

    elif text=="⏰ Horário Briefing":
        ctx.user_data["aguardando"]="h_briefing"
        await update.message.reply_text("⏰ Horário do briefing:\nEx: `07:30`",parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="📄 Horário PDF":
        ctx.user_data["aguardando"]="h_pdf"
        await update.message.reply_text("📄 Horário do PDF:\nEx: `18:00`",parse_mode="Markdown",reply_markup=main_keyboard())

    elif text=="❓ Ajuda":
        await update.message.reply_text(
            "🤖 *FinVest Agent v5*\n\n"
            "📊 *Mercado* — cotações rápidas\n🤖 *Análise* — análise instantânea\n"
            "📄 *PDF Agora* — relatório completo com:\n"
            "   • Análise narrativa do dia\n   • Notícias BR e Internacional\n   • Calendário econômico\n"
            "📱 *Gerar Post* — posts prontos\n🔔 *Alertas* — avisos de preço\n💰 *Simular* — rendimentos\n"
            "⚙️ *Configurar* — horários automáticos\n\n"
            "*Automático:*\n☀️ Briefing 8h | 📄 PDF 18h | 🔔 Alertas 30min\n\n"
            "*Criar alerta:* `alerta PETR4 acima 45`\n*Remover:* `remover 1`",
            parse_mode="Markdown",reply_markup=main_keyboard())

    else:
        aguardando=ctx.user_data.get("aguardando")

        if text.lower().startswith("alerta "):
            try:
                partes=text.lower().split()
                key=partes[1]; tipo=partes[2]; valor=float(partes[3])
                if key not in TICKERS:
                    await update.message.reply_text(f"❌ Ativo `{key}` não encontrado.",parse_mode="Markdown",reply_markup=main_keyboard()); return
                alertas=load_json(ALERTAS_FILE,[])
                import uuid
                alertas.append({"id":str(uuid.uuid4())[:8],"key":key,"tipo":tipo,"valor":valor})
                save_json(ALERTAS_FILE,alertas)
                await update.message.reply_text(f"✅ Alerta criado!\n*{key.upper()}* {tipo} `{fmt(valor,2)}`",
                    parse_mode="Markdown",reply_markup=main_keyboard())
            except:
                await update.message.reply_text("❌ Use: `alerta PETR4 acima 45`",parse_mode="Markdown",reply_markup=main_keyboard())

        elif text.lower().startswith("remover "):
            try:
                idx=int(text.split()[1])-1
                alertas=load_json(ALERTAS_FILE,[])
                if 0<=idx<len(alertas):
                    r=alertas.pop(idx); save_json(ALERTAS_FILE,alertas)
                    await update.message.reply_text(f"🗑 *{r['key'].upper()}* removido!",parse_mode="Markdown",reply_markup=main_keyboard())
            except:
                await update.message.reply_text("❌ Use: `remover 1`",parse_mode="Markdown",reply_markup=main_keyboard())

        elif aguardando=="simular":
            ctx.user_data["aguardando"]=None
            try:
                partes=text.strip().split()
                valor,anos,taxa=float(partes[0]),int(partes[1]),float(partes[2])
                resultado,lucro=simular(valor,anos,taxa)
                await update.message.reply_text(
                    f"💰 *Simulação*\n\n💵 Capital: `R$ {fmt(valor)}`\n📅 Período: `{anos} anos`\n📈 Taxa: `{taxa}% a.a.`\n\n"
                    f"━━━━━━━━━━━\n💎 *Resultado: R$ {fmt(resultado)}*\n📊 Lucro: `R$ {fmt(lucro)}`\n"
                    f"📈 Rendimento: `{fc(lucro/valor*100)}`\n\n_Sem IR ou taxas._",
                    parse_mode="Markdown",reply_markup=main_keyboard())
            except:
                await update.message.reply_text("❌ Use: `10000 5 14.75`",parse_mode="Markdown",reply_markup=main_keyboard())

        elif aguardando in["h_briefing","h_pdf"]:
            ctx.user_data["aguardando"]=None
            try:
                partes=text.strip().split(":")
                hora,minuto=int(partes[0]),int(partes[1])
                config=load_json(CONFIG_FILE,{}); jq=ctx.job_queue
                if aguardando=="h_briefing":
                    config["h_briefing"]=f"{hora:02d}:{minuto:02d}"
                    for j in jq.get_jobs_by_name("briefing"): j.schedule_removal()
                    jq.run_daily(job_briefing,time=dtime(hour=hora,minute=minuto),name="briefing")
                    label="Briefing"
                else:
                    config["h_pdf"]=f"{hora:02d}:{minuto:02d}"
                    for j in jq.get_jobs_by_name("pdf_fechamento"): j.schedule_removal()
                    jq.run_daily(job_pdf_fechamento,time=dtime(hour=hora,minute=minuto),name="pdf_fechamento")
                    label="PDF"
                save_json(CONFIG_FILE,config)
                await update.message.reply_text(f"✅ *{label}* configurado para *{hora:02d}:{minuto:02d}*!",
                    parse_mode="Markdown",reply_markup=main_keyboard())
            except:
                await update.message.reply_text("❌ Use: `08:00`",parse_mode="Markdown",reply_markup=main_keyboard())

        elif aguardando=="nova_tarefa":
            ctx.user_data["aguardando"]=None
            tarefas=load_json(TAREFAS_FILE,[])
            tarefas.append({"texto":text,"done":False})
            save_json(TAREFAS_FILE,tarefas)
            await update.message.reply_text(f"✅ *{text}* adicionada!",parse_mode="Markdown",reply_markup=main_keyboard())

        elif aguardando=="check_tarefa":
            ctx.user_data["aguardando"]=None
            try:
                idx=int(text.strip())-1
                tarefas=load_json(TAREFAS_FILE,[])
                if 0<=idx<len(tarefas):
                    tarefas[idx]["done"]=not tarefas[idx]["done"]
                    save_json(TAREFAS_FILE,tarefas)
                    status="concluída ✅" if tarefas[idx]["done"] else "desmarcada ⬜"
                    await update.message.reply_text(f"*{tarefas[idx]['texto']}* {status}!",
                        parse_mode="Markdown",reply_markup=main_keyboard())
            except:
                await update.message.reply_text("❌ Digite só o número.",reply_markup=main_keyboard())
        else:
            await update.message.reply_text("Use o menu 👇",reply_markup=main_keyboard())

# ══════════════════════════════════════════════
logging.basicConfig(level=logging.WARNING)

def main():
    print("🤖 FinVest Bot v5 iniciando...")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
    print("✅ Rodando! Abra o Telegram e mande /start")
    app.run_polling()

if __name__=="__main__":
    main()
