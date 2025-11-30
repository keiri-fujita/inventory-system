from flask import Flask, render_template, request, redirect, url_for, session
import csv
import os
from collections import defaultdict
import datetime  # ← これを追加
from datetime import date
import re

aapp = Flask(__name__)

# セッション用の秘密鍵（Render の環境変数から取得）
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
# ログイン用パスワード（Render の環境変数から取得）
APP_PASSWORD = os.environ.get("APP_PASSWORD", "demo-password")


# === 設定値 ===
# URL で使うスラッグ → 実際の拠点名（CSVファイル名にもなる）
BASE_SLUGS = {
    "kobe": "神戸",
    "yokohama": "横浜",
    "omiya": "大宮",
    "senboku": "泉北",
    "chiba": "千葉",
    "ateam": "Aチーム",
}

# ★ ここを追加（必ず左端から書く）
def get_base_name_from_slug(base_slug: str):
    """URL のスラッグから拠点名（日本語名）を取得する"""
    return BASE_SLUGS.get(base_slug)


# 既存処理で使っている拠点名リスト（値だけ取り出す）
BASE_NAMES = list(BASE_SLUGS.values())

DATA_DIR = "data"
LOG_FILE = os.path.join(DATA_DIR, "log.csv")

# 在庫CSVのヘッダー（拠点ごと）
HEADERS = [
    "No.", "出庫", "地金", "アイテム", "中石", "サイズ", "品番",
    "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日", "下代（数値）"
]

# ログCSVのヘッダー
# 出庫ログ用に「出庫日」「メモ」を追加
LOG_HEADERS = [
    "処理", "拠点",
    "No.", "地金", "アイテム", "中石", "サイズ", "品番",
    "上代", "下代", "脇石", "チェーン長", "摘要", "入力者",
    "入庫日", "出庫日", "メモ", "下代（数値）"
]

# === 共通関数 ===
def _to_int(x):
    """文字列を安全に整数化（カンマ対応）"""
    if x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        return 0


def load_inventory(base_name):
    """拠点CSV読み込み"""
    path = os.path.join(DATA_DIR, f"{base_name}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))[1:]  # ヘッダー行除外


def save_inventory(base_name, rows):
    """拠点CSV書き込み"""
    path = os.path.join(DATA_DIR, f"{base_name}.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for i, row in enumerate(rows, start=1):
            row[0] = str(i)  # No. を振り直す
            writer.writerow(row)


def append_log(row, mode, base_name=None):
    """
    出庫・入庫ログ記録用
    row       : 拠点在庫CSVの1行（HEADERS 順）
    mode      : "入庫" or "出庫"
    base_name : 拠点名
    """

    # row は HEADERS に準拠している想定：
    # [No., 出庫, 地金, アイテム, 中石, サイズ, 品番,
    #  上代, 下代, 脇石, チェーン長, 摘要, 入力者, 入庫日, 下代（数値）]

    def safe_get(idx):
        return row[idx] if len(row) > idx else ""

    no_           = safe_get(0)
    jigan         = safe_get(2)
    item          = safe_get(3)
    chuseki       = safe_get(4)
    size          = safe_get(5)
    hinban        = safe_get(6)
    uedai         = safe_get(7)
    gedai         = safe_get(8)
    wakishi       = safe_get(9)
    chain_len     = safe_get(10)
    tekiyo        = safe_get(11)
    input_user    = safe_get(12)
    nyuko_date    = safe_get(13)
    gedai_numeric = safe_get(14)

    # 出庫ログのときだけ出庫日を今日の日付にする
    if mode == "出庫":
        shukko_date = date.today().strftime("%Y/%m/%d")
    else:
        shukko_date = ""

    # ★ メモ列は最初は空文字にしておく
    memo_text = ""

    log_row = [
        mode,
        base_name or "",
        no_,
        jigan,
        item,
        chuseki,
        size,
        hinban,
        uedai,
        gedai,
        wakishi,
        chain_len,
        tekiyo,
        input_user,
        nyuko_date,
        shukko_date,
        memo_text,
        gedai_numeric,
    ]

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(log_row)



def load_log_rows(mode=None):
    """
    log.csv を読み込み、必要なら処理種別(mode)でフィルタし、
    新しいものが上に来るように降順に並べる。
    mode: None → 全件, "入庫" or "出庫" → 絞り込み
    """
    if not os.path.exists(LOG_FILE):
        return []

    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if not rows:
        return []

    # 先頭行がヘッダーの場合は除外
    if rows[0] == LOG_HEADERS:
        rows = rows[1:]

    if mode:
        rows = [r for r in rows if r and r[0] == mode]

    # 末尾に追記しているので、逆順にして新しいものを上に
    rows = rows[::-1]
    return rows


def summarize_inventory(rows):
    """行配列の形（単拠点/全拠点）に応じて列位置を切り替えて集計"""
    cats = ["リング", "ペンダント", "チェーン", "その他"]
    summary = {c: {"count": 0, "上代": 0, "下代": 0} for c in cats}
    totals = {"count": 0, "上代": 0, "下代": 0}

    for row in rows:
        if not row:
            continue

        # --- 列インデックスを形に応じて決定 ---
        # 単拠点: 長さ>=15（No.,出庫を含む）
        # 全拠点: 長さ==13（先頭=地金）
        if len(row) >= 15:
            idx_item = 3   # アイテム
            idx_up   = 7   # 上代
            idx_down = 8   # 下代
        elif len(row) == 13:
            idx_item = 1   # アイテム
            idx_up   = 5   # 上代
            idx_down = 6   # 下代
        else:
            # 念のためのフォールバック（列数が想定外のときはスキップ）
            continue

        try:
            item = str(row[idx_item])
            up_str = str(row[idx_up]).replace(",", "").strip()
            dn_str = str(row[idx_down]).replace(",", "").strip()
            up_val = float(up_str) if up_str else 0.0
            dn_val = float(dn_str) if dn_str else 0.0
        except Exception:
            continue

        # カテゴリ判定（含まれていれば該当）
        if "リング" in item:
            cat = "リング"
        elif "ペンダント" in item:
            cat = "ペンダント"
        elif "チェーン" in item:
            cat = "チェーン"
        else:
            cat = "その他"

        summary[cat]["count"] += 1
        summary[cat]["上代"] += up_val
        summary[cat]["下代"] += dn_val

        totals["count"] += 1
        totals["上代"] += up_val
        totals["下代"] += dn_val

    # 表示整形
    for c in cats:
        summary[c]["上代"] = f"{summary[c]['上代']:,.0f}"
        summary[c]["下代"] = f"{summary[c]['下代']:,.0f}"
    totals["上代"] = f"{totals['上代']:,.0f}"
    totals["下代"] = f"{totals['下代']:,.0f}"

    return summary, totals


# app.py の先頭あたりに追加
BASES = [
    {
        "slug": "yokohama",
        "label_entry": "横浜店",     # 入庫メニュー用の表示
        "label_inventory": "横浜店"  # 在庫一覧での表示
    },
    {
        "slug": "kobe",
        "label_entry": "神戸店",
        "label_inventory": "神戸店"
    },
    {
        "slug": "omiya",
        "label_entry": "大宮店",
        "label_inventory": "大宮店"
    },
    {
        "slug": "senboku",
        "label_entry": "泉北店",
        "label_inventory": "泉北店"
    },
    {
        "slug": "chiba",
        "label_entry": "千葉店",
        "label_inventory": "千葉店"
    },
    {
        "slug": "ateam",
        "label_entry": "Aチーム",   # ここは「店」を付けない
        "label_inventory": "Aチーム"
    },
]

@app.before_request
def require_login():
    # ログインページと静的ファイルはスルー
    if request.endpoint in ("login", "static"):
        return

    # すでにログイン済みならOK
    if session.get("logged_in"):
        return

    # それ以外はログインページへ
    return redirect(url_for("login"))




# === ルーティング ===

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == APP_PASSWORD:
            session["logged_in"] = True
            # トップページの関数名に合わせる（ここでは index と仮定）
            return redirect(url_for("index"))
        else:
            error = "パスワードが違います。"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/print/base/<base_name>")
def print_base_inventory(base_name):
    if base_name not in BASE_NAMES:
        return "拠点が見つかりません", 404

    # すでに使っている共通関数をそのまま使う
    rows = load_inventory(base_name)

    return render_template(
        "inventory_base_print.html",
        base_name=base_name,
        rows=rows,
    )


@app.route("/")
def index():
    return render_template("index.html", bases=BASES)

@app.route("/inventory/<base_name>", methods=["GET", "POST"])
def inventory(base_name):
    if base_name not in BASE_NAMES:
        return "拠点が見つかりません", 404

    # 対象拠点の在庫を読み込み
    rows = load_inventory(base_name)

    # ---- 出庫処理 ----
    if request.method == "POST":
        checked = request.form.getlist("checkout")  # チェックされた行の index
        new_rows = []
        for i, row in enumerate(rows):
            if str(i) in checked:
                # 出庫ログ記録（拠点名つき）
                append_log(row, "出庫", base_name)
            else:
                new_rows.append(row)
        save_inventory(base_name, new_rows)
        rows = new_rows  # 出庫後の在庫に更新

    # ==== 表示用ヘッダー ====
    headers = [
        "No.", "地金", "アイテム", "中石", "サイズ", "品番",
        "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日"
    ]

    # ==== 集計（リング/ペンダント/チェーン/その他） ====
    summary, totals = summarize_inventory(rows)

    # inventory.html を表示
    return render_template(
        "inventory.html",
        base_name=base_name,
        headers=headers,
        rows=rows,
        enumerate=enumerate,
        summary=summary,
        totals=totals,
        total_count=totals["count"],
        total_上代=totals["上代"],
        total_下代=totals["下代"],
    )


@app.route("/inventory/<base_name>/edit/<no>", methods=["GET", "POST"])
def edit_inventory_row(base_name, no):
    """拠点在庫1行分の編集用"""

    if base_name not in BASE_NAMES:
        return "拠点が見つかりません", 404

    # 対象拠点の在庫を読み込み
    rows = load_inventory(base_name)

    # No. が一致する行を探す
    target_index = None
    for idx, row in enumerate(rows):
        if len(row) > 0 and row[0] == str(no):
            target_index = idx
            break

    if target_index is None:
        return f"No.{no} の在庫が見つかりません", 404

    row = rows[target_index]

    if request.method == "POST":
        # 全拠点画面から来たかどうか（hidden で送られてくる）
        from_all = (request.form.get("from_all") == "1")

        # フォームから値を取得
        jigan      = request.form.get("jigan", "").strip()
        item       = request.form.get("item", "").strip()
        chuseki    = request.form.get("chuseki", "").strip()
        size       = request.form.get("size", "").strip()
        hinban     = request.form.get("hinban", "").strip()
        uedai      = request.form.get("uedai", "").strip()
        gedai      = request.form.get("gedai", "").strip()
        wakishi    = request.form.get("wakishi", "").strip()
        chain_len  = request.form.get("chain_len", "").strip()
        tekiyo     = request.form.get("tekiyo", "").strip()
        input_user = request.form.get("input_user", "").strip()
        nyuko_date = request.form.get("nyuko_date", "").strip()

        # 必須チェック
        required = {
            "地金": jigan,
            "アイテム": item,
            "中石": chuseki,
            "サイズ": size,
            "品番": hinban,
            "上代": uedai,
            "暗号化下代": gedai,
            "入力者": input_user,
        }
        missing = [name for name, val in required.items() if not val]
        if missing:
            flash("必須項目が不足しています。", "error")

            # そのまま編集画面を再表示
            return render_template(
                "inventory_edit.html",
                base_name=base_name,
                no=no,
                from_all=from_all,
                row={
                    "jigan": jigan,
                    "item": item,
                    "chuseki": chuseki,
                    "size": size,
                    "hinban": hinban,
                    "uedai": uedai,
                    "gedai": gedai,
                    "wakishi": wakishi,
                    "chain_len": chain_len,
                    "tekiyo": tekiyo,
                    "input_user": input_user,
                    "nyuko_date": nyuko_date,
                },
            )

        # 入庫日：YYYY-MM-DD → YYYY/MM/DD に揃える（他の形式はそのまま）
        if nyuko_date:
            try:
                d = datetime.datetime.strptime(nyuko_date, "%Y-%m-%d")
                nyuko_date = d.strftime("%Y/%m/%d")
            except ValueError:
                nyuko_date = nyuko_date.replace("-", "/")

        # 既存の No. / 出庫フラグ / 下代（数値）はそのまま使う
        no_           = row[0] if len(row) > 0 else ""
        shukko_flag   = row[1] if len(row) > 1 else ""
        gedai_numeric = row[14] if len(row) > 14 else ""

        new_row = [
            no_,
            shukko_flag,
            jigan,
            item,
            chuseki,
            size,
            hinban,
            uedai,
            gedai,
            wakishi,
            chain_len,
            tekiyo,
            input_user,
            nyuko_date,
            gedai_numeric,
        ]

        rows[target_index] = new_row
        save_inventory(base_name, rows)

        # ★ メッセージは「戻り先の一覧」で出す
        flash(f"No.{no} の在庫を更新しました。", "success")

        if from_all:
            return redirect(url_for("inventory_all"))
        else:
            return redirect(url_for("inventory", base_name=base_name))

    # GET: 編集フォーム表示
    from_all = (request.args.get("from_all") == "1")

    row_dict = {
        "jigan":      row[2] if len(row) > 2 else "",
        "item":       row[3] if len(row) > 3 else "",
        "chuseki":    row[4] if len(row) > 4 else "",
        "size":       row[5] if len(row) > 5 else "",
        "hinban":     row[6] if len(row) > 6 else "",
        "uedai":      row[7] if len(row) > 7 else "",
        "gedai":      row[8] if len(row) > 8 else "",
        "wakishi":    row[9] if len(row) > 9 else "",
        "chain_len":  row[10] if len(row) > 10 else "",
        "tekiyo":     row[11] if len(row) > 11 else "",
        "input_user": row[12] if len(row) > 12 else "",
        "nyuko_date": row[13] if len(row) > 13 else "",
    }

    return render_template(
        "inventory_edit.html",
        base_name=base_name,
        no=no,
        from_all=from_all,
        row=row_dict,
    )


@app.route("/inventory_all")
def inventory_all():
    """全拠点の在庫を統合して表示（No.・出庫は画面には出さない）"""
    all_rows = []

    for base in BASE_NAMES:
        rows = load_inventory(base)
        for row in rows:
            if len(row) < 15:
                continue
            # row:
            # [No., 出庫, 地金, アイテム, 中石, サイズ, 品番,
            #  上代, 下代, 脇石, チェーン長, 摘要, 入力者, 入庫日, 下代（数値）]
            full_row = [base] + row   # 先頭に拠点名を追加
            all_rows.append(full_row)

    # 集計用：拠点列だけ除いた「元の形」を summarize_inventory に渡す
    rows_for_summary = [r[1:] for r in all_rows]
    summary, totals = summarize_inventory(rows_for_summary)

    headers = [
        "拠点", "地金", "アイテム", "中石", "サイズ", "品番",
        "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日"
    ]

    return render_template(
        "inventory_all.html",
        headers=headers,
        rows=all_rows,
        summary=summary,
        totals=totals,
        total_count=totals["count"],
        total_上代=totals["上代"],
        total_下代=totals["下代"],
    )


@app.route("/add_stock_for_base/<base_slug>", methods=["GET", "POST"])
def add_stock_for_base(base_slug):
    # スラッグから拠点名を取得
    base_name = get_base_name_from_slug(base_slug)
    if not base_name:
        return "拠点が見つかりません", 404

    error = None
    success = None
    ROW_COUNT = 20

    if request.method == "POST":
        # ▼ 全拠点フォームと同じ項目を取得（branch は使わない）
        _branches       = request.form.getlist("branch[]")  # 受け取るが使わない
        jigan_list      = request.form.getlist("jigan[]")
        item_list       = request.form.getlist("item[]")
        chuseki_list    = request.form.getlist("chuseki[]")
        size_list       = request.form.getlist("size[]")
        hinban_list     = request.form.getlist("hinban[]")
        uedai_list      = request.form.getlist("uedai[]")
        gedai_list      = request.form.getlist("gedai[]")
        wakishi_list    = request.form.getlist("wakishi[]")
        chain_list      = request.form.getlist("chain_len[]")
        tekiyo_list     = request.form.getlist("tekiyo[]")
        input_user_list = request.form.getlist("input_user[]")
        nyuko_date_list = request.form.getlist("nyuko_date[]")
        gedai_num_list  = request.form.getlist("gedai_numeric[]")

        # --- rows_data（エラー時の再表示用）を作成：branch は常に base_name で固定 ---
        rows_data = []
        max_len = max(
            len(jigan_list),
            len(item_list),
            len(chuseki_list),
            len(size_list),
            len(hinban_list),
            len(uedai_list),
            len(gedai_list),
            len(wakishi_list),
            len(chain_list),
            len(tekiyo_list),
            len(input_user_list),
            len(nyuko_date_list),
            len(gedai_num_list),
            ROW_COUNT
        )
        for i in range(max_len):
            rows_data.append({
                "branch":        base_name,                         # ★固定
                "jigan":         jigan_list[i]      if i < len(jigan_list)      else "",
                "item":          item_list[i]       if i < len(item_list)       else "",
                "chuseki":       chuseki_list[i]    if i < len(chuseki_list)    else "",
                "size":          size_list[i]       if i < len(size_list)       else "",
                "hinban":        hinban_list[i]     if i < len(hinban_list)     else "",
                "uedai":         uedai_list[i]      if i < len(uedai_list)      else "",
                "gedai":         gedai_list[i]      if i < len(gedai_list)      else "",
                "wakishi":       wakishi_list[i]    if i < len(wakishi_list)    else "",
                "chain_len":     chain_list[i]      if i < len(chain_list)      else "",
                "tekiyo":        tekiyo_list[i]     if i < len(tekiyo_list)     else "",
                "input_user":    input_user_list[i] if i < len(input_user_list) else "",
                "nyuko_date":    nyuko_date_list[i] if i < len(nyuko_date_list) else "",
                "gedai_numeric": gedai_num_list[i]  if i < len(gedai_num_list)  else "",
            })
        if len(rows_data) < ROW_COUNT:
            for _ in range(ROW_COUNT - len(rows_data)):
                rows_data.append({
                    "branch": base_name, "jigan": "", "item": "", "chuseki": "",
                    "size": "", "hinban": "", "uedai": "", "gedai": "",
                    "wakishi": "", "chain_len": "", "tekiyo": "",
                    "input_user": "", "nyuko_date": "", "gedai_numeric": ""
                })

        # このフォームでは対象拠点は1つだけ
        per_base_rows = {base_name: load_inventory(base_name)}
        today_str_dash = datetime.date.today().strftime("%Y-%m-%d")

        # 単位正規化用：脇石(ct) / チェーン長(cm)
        def normalize_ct(s: str) -> str:
            s = s.strip()
            if not s:
                return ""
            lower = s.lower()
            if "ct" in lower or "ｃｔ" in lower:
                s2 = re.sub(r"[ｃcＣC][ｔtＴT]", "ct", s, flags=re.IGNORECASE)
                return s2
            if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s):
                return f"{s}ct"
            return s

        def normalize_cm(s: str) -> str:
            s = s.strip()
            if not s:
                return ""
            s2 = re.sub(r"[cｃＣ][mｍＭ]|㎝", "cm", s, flags=re.IGNORECASE)
            lower = s2.lower()
            if "cm" in lower:
                return s2
            if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s2):
                return f"{s2}cm"
            return s2

        rows_added = 0

        # --- 登録処理 & バリデーション ---
        for i in range(ROW_COUNT):
            # ★ 拠点は URL で固定
            branch   = base_name
            jigan    = jigan_list[i].strip()      if i < len(jigan_list)      else ""
            item     = item_list[i].strip()       if i < len(item_list)       else ""
            chuseki  = chuseki_list[i].strip()    if i < len(chuseki_list)    else ""
            size     = size_list[i].strip()       if i < len(size_list)       else ""
            hinban   = hinban_list[i].strip()     if i < len(hinban_list)     else ""
            uedai    = uedai_list[i].strip()      if i < len(uedai_list)      else ""
            gedai    = gedai_list[i].strip()      if i < len(gedai_list)      else ""
            wakishi  = wakishi_list[i].strip()    if i < len(wakishi_list)    else ""
            chain    = chain_list[i].strip()      if i < len(chain_list)      else ""
            tekiyo   = tekiyo_list[i].strip()     if i < len(tekiyo_list)     else ""
            input_usr= input_user_list[i].strip() if i < len(input_user_list) else ""
            nyuko_dt = nyuko_date_list[i].strip() if i < len(nyuko_date_list) else ""
            gedai_num= gedai_num_list[i].strip()  if i < len(gedai_num_list)  else ""

            # 完全な空行はスキップ（branch は必ず入っているので他の項目で判定）
            if not (jigan or item or chuseki or size or hinban or uedai or gedai or input_usr or nyuko_dt):
                continue

            # 必須項目チェック（脇石 / チェーン長 / 摘要 以外）
            required = {
                "地金": jigan,
                "アイテム": item,
                "中石": chuseki,
                "サイズ": size,
                "品番": hinban,
                "上代": uedai,
                "暗号化下代": gedai,
                "入力者": input_usr,
            }
            missing = [name for name, val in required.items() if not val]
            if missing:
                error = "必須項目が不足しています。"
                break

            # 入庫日：空なら今日。フォーマットを YYYY/MM/DD に変換
            if not nyuko_dt:
                nyuko_dt = today_str_dash
            try:
                d = datetime.datetime.strptime(nyuko_dt, "%Y-%m-%d")
                nyuko_dt = d.strftime("%Y/%m/%d")
            except ValueError:
                nyuko_dt = nyuko_dt.replace("-", "/")

            # 中石=ダイヤ のとき、サイズの数値 >=1 を CT 表記に変換
            size_for_store = size
            if chuseki == "ダイヤ" and re.fullmatch(r"[0-9]+(\.[0-9]+)?", size):
                val = float(size)
                if val == 1:
                    size_for_store = "CT"
                elif val > 1:
                    size_for_store = f"{val:g}CT"

            wakishi_clean = normalize_ct(wakishi)
            chain_clean   = normalize_cm(chain)

            row = [
                "",             # No.
                "",             # 出庫
                jigan,
                item,
                chuseki,
                size_for_store,
                hinban,
                uedai,
                gedai,
                wakishi_clean,
                chain_clean,
                tekiyo,
                input_usr,
                nyuko_dt,
                gedai_num,
            ]

            per_base_rows[branch].append(row)
            append_log(row, "入庫", branch)
            rows_added += 1

        if error:
            flash(error, "error")
            return render_template(
                "add_stock.html",
                base_names=BASE_NAMES,
                rows_data=rows_data,
                error=error,
                success=None,
                fixed_base=base_name,   # ★ テンプレ側で「拠点固定」に使う
            )

        if rows_added == 0:
            flash("入庫対象の行がありませんでした。", "error")
            return render_template(
                "add_stock.html",
                base_names=BASE_NAMES,
                rows_data=rows_data,
                error=None,
                success=None,
                fixed_base=base_name,
            )

        # --- 並べ替え（単一拠点のみ） ---
        ITEM_ORDER = ["リング", "ペンダント", "バチカン", "チェーン", "その他"]
        ITEM_RANK = {name: idx for idx, name in enumerate(ITEM_ORDER)}

        JIGAN_ORDER = [
            "Pt900",
            "Pt850",
            "K18",
            "SV900(Pt)",
            "Pt900/K18",
            "Pt900/K18/K18WG",
            "Pt900/K18/K18PG",
            "K18WG",
            "K18PG",
        ]
        JIGAN_RANK = {name: idx for idx, name in enumerate(JIGAN_ORDER)}

        CHU_SEKI_ORDER = ["ダイヤ", "オーバル", "パール", "スクエア", "Free", "チェーン"]
        CHU_SEKI_RANK = {name: idx for idx, name in enumerate(CHU_SEKI_ORDER)}

        def item_rank(val):
            return ITEM_RANK.get(val, len(ITEM_ORDER))

        def jigan_rank(val):
            return JIGAN_RANK.get(val, len(JIGAN_ORDER))

        def chuseki_rank(val):
            return CHU_SEKI_RANK.get(val, len(CHU_SEKI_ORDER))

        def parse_size_for_sort(s):
            if s is None:
                return 0.0
            t = str(s).strip()
            if not t:
                return 0.0
            upper = t.upper()
            if "CT" in upper:
                m = re.search(r"([0-9]+(\.[0-9]+)?)", upper)
                if m:
                    return float(m.group(1))
                return 1.0
            try:
                return float(t)
            except ValueError:
                return 0.0

        def parse_price(s):
            t = str(s).replace(",", "").strip()
            try:
                return float(t)
            except:
                return 0.0

        def sort_rows(rows):
            return sorted(
                rows,
                key=lambda r: (
                    item_rank(r[3]),
                    jigan_rank(r[2]),
                    chuseki_rank(r[4]),
                    parse_size_for_sort(r[5]),
                    str(r[6]),
                    parse_price(r[7]),
                )
            )

        rows_sorted = sort_rows(per_base_rows[base_name])
        save_inventory(base_name, rows_sorted)

        flash(f"{rows_added} 件を {base_name} に入庫しました", "success")
        return redirect(url_for("add_stock_for_base", base_slug=base_slug))

    # GET（初回表示）: 全行 branch は固定拠点名で埋める
    ROW_COUNT = 20
    rows_data = []
    for _ in range(ROW_COUNT):
        rows_data.append({
            "branch": base_name, "jigan": "", "item": "", "chuseki": "",
            "size": "", "hinban": "", "uedai": "", "gedai": "",
            "wakishi": "", "chain_len": "", "tekiyo": "",
            "input_user": "", "nyuko_date": "", "gedai_numeric": ""
        })

    return render_template(
        "add_stock.html",
        base_names=BASE_NAMES,
        rows_data=rows_data,
        error=None,
        success=None,
        fixed_base=base_name,
    )


@app.route("/add_stock", methods=["GET", "POST"])
def add_stock():
    """
    全拠点共通の入庫フォーム（最大20行）。
    （元の処理そのまま：拠点選択あり）
    """
    error = None
    success = None
    ROW_COUNT = 20

    if request.method == "POST":
        branches        = request.form.getlist("branch[]")
        jigan_list      = request.form.getlist("jigan[]")
        item_list       = request.form.getlist("item[]")
        chuseki_list    = request.form.getlist("chuseki[]")
        size_list       = request.form.getlist("size[]")
        hinban_list     = request.form.getlist("hinban[]")
        uedai_list      = request.form.getlist("uedai[]")
        gedai_list      = request.form.getlist("gedai[]")
        wakishi_list    = request.form.getlist("wakishi[]")
        chain_list      = request.form.getlist("chain_len[]")
        tekiyo_list     = request.form.getlist("tekiyo[]")
        input_user_list = request.form.getlist("input_user[]")
        nyuko_date_list = request.form.getlist("nyuko_date[]")
        gedai_num_list  = request.form.getlist("gedai_numeric[]")

        # --- 今回の入力内容を rows_data にまとめる（エラー時に再表示用） ---
        rows_data = []
        max_len = max(
            len(branches),
            len(jigan_list),
            len(item_list),
            len(chuseki_list),
            len(size_list),
            len(hinban_list),
            len(uedai_list),
            len(gedai_list),
            len(wakishi_list),
            len(chain_list),
            len(tekiyo_list),
            len(input_user_list),
            len(nyuko_date_list),
            len(gedai_num_list),
            ROW_COUNT
        )
        for i in range(max_len):
            rows_data.append({
                "branch":        branches[i]        if i < len(branches)        else "",
                "jigan":         jigan_list[i]      if i < len(jigan_list)      else "",
                "item":          item_list[i]       if i < len(item_list)       else "",
                "chuseki":       chuseki_list[i]    if i < len(chuseki_list)    else "",
                "size":          size_list[i]       if i < len(size_list)       else "",
                "hinban":        hinban_list[i]     if i < len(hinban_list)     else "",
                "uedai":         uedai_list[i]      if i < len(uedai_list)      else "",
                "gedai":         gedai_list[i]      if i < len(gedai_list)      else "",
                "wakishi":       wakishi_list[i]    if i < len(wakishi_list)    else "",
                "chain_len":     chain_list[i]      if i < len(chain_list)      else "",
                "tekiyo":        tekiyo_list[i]     if i < len(tekiyo_list)     else "",
                "input_user":    input_user_list[i] if i < len(input_user_list) else "",
                "nyuko_date":    nyuko_date_list[i] if i < len(nyuko_date_list) else "",
                "gedai_numeric": gedai_num_list[i]  if i < len(gedai_num_list)  else "",
            })
        if len(rows_data) < ROW_COUNT:
            for _ in range(ROW_COUNT - len(rows_data)):
                rows_data.append({
                    "branch": "", "jigan": "", "item": "", "chuseki": "",
                    "size": "", "hinban": "", "uedai": "", "gedai": "",
                    "wakishi": "", "chain_len": "", "tekiyo": "",
                    "input_user": "", "nyuko_date": "", "gedai_numeric": ""
                })

        # 在庫読み込み
        per_base_rows = {base: load_inventory(base) for base in BASE_NAMES}
        today_str_dash = datetime.date.today().strftime("%Y-%m-%d")

        # 単位正規化用：脇石(ct) / チェーン長(cm)
        def normalize_ct(s: str) -> str:
            """脇石用：単位を ct に統一する"""
            s = s.strip()
            if not s:
                return ""
            lower = s.lower()
            # すでに ct 系が入っていれば表記だけ揃える → "ct"
            if "ct" in lower or "ｃｔ" in lower:
                s2 = re.sub(r"[ｃcＣC][ｔtＴT]", "ct", s, flags=re.IGNORECASE)
                return s2
            # 数値だけなら "◯ct" を付与（例: "1.5" → "1.5ct"）
            if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s):
                return f"{s}ct"
            return s

        def normalize_cm(s: str) -> str:
            s = s.strip()
            if not s:
                return ""
            # 各種 cm 表記を "cm" に揃える
            s2 = re.sub(r"[cｃＣ][mｍＭ]|㎝", "cm", s, flags=re.IGNORECASE)
            lower = s2.lower()
            if "cm" in lower:
                return s2
            # 数値だけなら "◯cm" を付与
            if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s2):
                return f"{s2}cm"
            return s2

        rows_added = 0  # 何件入庫したかカウント

        # --- 登録処理 & バリデーション ---
        for i in range(ROW_COUNT):
            branch   = branches[i].strip()        if i < len(branches)        else ""
            jigan    = jigan_list[i].strip()      if i < len(jigan_list)      else ""
            item     = item_list[i].strip()       if i < len(item_list)       else ""
            chuseki  = chuseki_list[i].strip()    if i < len(chuseki_list)    else ""
            size     = size_list[i].strip()       if i < len(size_list)       else ""
            hinban   = hinban_list[i].strip()     if i < len(hinban_list)     else ""
            uedai    = uedai_list[i].strip()      if i < len(uedai_list)      else ""
            gedai    = gedai_list[i].strip()      if i < len(gedai_list)      else ""
            wakishi  = wakishi_list[i].strip()    if i < len(wakishi_list)    else ""
            chain    = chain_list[i].strip()      if i < len(chain_list)      else ""
            tekiyo   = tekiyo_list[i].strip()     if i < len(tekiyo_list)     else ""
            input_usr= input_user_list[i].strip() if i < len(input_user_list) else ""
            nyuko_dt = nyuko_date_list[i].strip() if i < len(nyuko_date_list) else ""
            gedai_num= gedai_num_list[i].strip()  if i < len(gedai_num_list)  else ""

            # 完全な空行はスキップ
            if not (branch or jigan or item or chuseki or size or hinban or uedai or gedai or input_usr or nyuko_dt):
                continue

            # 拠点チェック
            if branch not in BASE_NAMES:
                error = f"{i+1}行目：拠点が正しく選択されていません。"
                break

            # 必須項目チェック（脇石 / チェーン長 / 摘要 以外）
            required = {
                "地金": jigan,
                "アイテム": item,
                "中石": chuseki,
                "サイズ": size,
                "品番": hinban,
                "上代": uedai,
                "暗号化下代": gedai,
                "入力者": input_usr,
            }
            missing = [name for name, val in required.items() if not val]
            if missing:
                error = "必須項目が不足しています。"
                break

            # 入庫日：空なら今日。フォーマットを YYYY/MM/DD に変換
            if not nyuko_dt:
                nyuko_dt = today_str_dash
            try:
                d = datetime.datetime.strptime(nyuko_dt, "%Y-%m-%d")
                nyuko_dt = d.strftime("%Y/%m/%d")
            except ValueError:
                nyuko_dt = nyuko_dt.replace("-", "/")

            # ★ 中石=ダイヤ のとき、サイズの数値 >=1 を CT 表記に変換
            size_for_store = size
            if chuseki == "ダイヤ" and re.fullmatch(r"[0-9]+(\.[0-9]+)?", size):
                val = float(size)
                if val == 1:
                    size_for_store = "CT"
                elif val > 1:
                    # 1.0 → "1CT", 1.5 → "1.5CT"
                    size_for_store = f"{val:g}CT"
                # 1未満(0.9など)はそのまま size_for_store = size

            # 脇石 / チェーン長 の単位を整える
            wakishi_clean = normalize_ct(wakishi)
            chain_clean   = normalize_cm(chain)

            # 在庫CSV 1行分（HEADERS 準拠）
            row = [
                "",             # No.
                "",             # 出庫
                jigan,
                item,
                chuseki,
                size_for_store,
                hinban,
                uedai,
                gedai,          # 暗号化下代
                wakishi_clean,
                chain_clean,
                tekiyo,
                input_usr,
                nyuko_dt,
                gedai_num,
            ]

            per_base_rows[branch].append(row)
            append_log(row, "入庫", branch)
            rows_added += 1

        # --- ここから結果判定＆レスポンス ---

        if error:
            # エラー時：flash して入力を保持したまま再表示
            flash(error, "error")
            return render_template(
                "add_stock.html",
                base_names=BASE_NAMES,
                rows_data=rows_data,
                error=error,
                success=None
            )

        if rows_added == 0:
            # 1件も有効行がなかった場合
            flash("入庫対象の行がありませんでした。", "error")
            return render_template(
                "add_stock.html",
                base_names=BASE_NAMES,
                rows_data=rows_data,
                error=None,
                success=None
            )

        # --- 並べ替え（カスタムルール）---

        ITEM_ORDER = ["リング", "ペンダント", "バチカン", "チェーン", "その他"]
        ITEM_RANK = {name: idx for idx, name in enumerate(ITEM_ORDER)}

        JIGAN_ORDER = [
            "Pt900",
            "Pt850",
            "K18",
            "SV900(Pt)",
            "Pt900/K18",
            "Pt900/K18/K18WG",
            "Pt900/K18/K18PG",
            "K18WG",
            "K18PG",
        ]
        JIGAN_RANK = {name: idx for idx, name in enumerate(JIGAN_ORDER)}

        CHU_SEKI_ORDER = ["ダイヤ", "オーバル", "パール", "スクエア", "Free", "チェーン"]
        CHU_SEKI_RANK = {name: idx for idx, name in enumerate(CHU_SEKI_ORDER)}

        def item_rank(val):
            return ITEM_RANK.get(val, len(ITEM_ORDER))

        def jigan_rank(val):
            return JIGAN_RANK.get(val, len(JIGAN_ORDER))

        def chuseki_rank(val):
            return CHU_SEKI_RANK.get(val, len(CHU_SEKI_ORDER))

        def parse_size_for_sort(s):
            """サイズ（数値・CTを数値化）"""
            if s is None:
                return 0.0
            t = str(s).strip()
            if not t:
                return 0.0
            upper = t.upper()

            if "CT" in upper:
                m = re.search(r"([0-9]+(\.[0-9]+)?)", upper)
                if m:
                    return float(m.group(1))
                return 1.0
            try:
                return float(t)
            except ValueError:
                return 0.0

        def parse_price(s):
            t = str(s).replace(",", "").strip()
            try:
                return float(t)
            except:
                return 0.0

        def sort_rows(rows):
            """最終ソートキー"""
            return sorted(
                rows,
                key=lambda r: (
                    item_rank(r[3]),             # アイテム
                    jigan_rank(r[2]),            # 地金
                    chuseki_rank(r[4]),          # 中石
                    parse_size_for_sort(r[5]),   # サイズ
                    str(r[6]),                   # 品番
                    parse_price(r[7]),           # 上代
                )
            )

        for base, rows in per_base_rows.items():
            rows_sorted = sort_rows(rows)
            save_inventory(base, rows_sorted)

        # ★ 成功メッセージ（rows_added を使う！）
        flash(f"{rows_added} 件を入庫しました", "success")
        return redirect(url_for("add_stock"))

    # GET（初回表示）
    ROW_COUNT = 20
    rows_data = []
    for _ in range(ROW_COUNT):
        rows_data.append({
            "branch": "", "jigan": "", "item": "", "chuseki": "",
            "size": "", "hinban": "", "uedai": "", "gedai": "",
            "wakishi": "", "chain_len": "", "tekiyo": "",
            "input_user": "", "nyuko_date": "", "gedai_numeric": ""
        })

    return render_template(
        "add_stock.html",
        base_names=BASE_NAMES,
        rows_data=rows_data,
        error=None,
        success=None
    )


@app.route("/log_in")
def log_in():
    # "入庫" の行だけを新しい順で取得
    rows = load_log_rows("入庫")

    # テンプレ側で LOG_HEADERS から不要列を隠す
    return render_template(
        "log_in.html",
        title="入庫ログ",
        headers=LOG_HEADERS,
        rows=rows,
    )


@app.route("/log_out", methods=["GET", "POST"])
def log_out():
    # メモ保存（POST）の処理
    if request.method == "POST":
        row_indices = request.form.getlist("row_index[]")
        memos       = request.form.getlist("memo[]")

        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, newline="", encoding="utf-8") as f:
                all_rows = list(csv.reader(f))

            # 「メモ」列のインデックスを取得
            try:
                memo_col = LOG_HEADERS.index("メモ")
            except ValueError:
                memo_col = None

            if memo_col is not None:
                for idx_str, memo in zip(row_indices, memos):
                    if not idx_str:
                        continue
                    try:
                        i = int(idx_str)
                    except ValueError:
                        continue

                    if i < 0 or i >= len(all_rows):
                        continue

                    row = all_rows[i]

                    # 行の長さが足りなければ埋めておく
                    if len(row) < len(LOG_HEADERS):
                        row = row + [""] * (len(LOG_HEADERS) - len(row))

                    row[memo_col] = memo
                    all_rows[i] = row

                # ログを書き戻す
                with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows(all_rows)

        # 保存後は再読み込み
        return redirect(url_for("log_out"))

    # GET: 出庫ログを読み込み
    display_rows = []
    row_indices  = []

    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, newline="", encoding="utf-8") as f:
            all_rows = list(csv.reader(f))

        # 先頭がヘッダーならスキップ
        start_idx = 1 if all_rows and all_rows[0] == LOG_HEADERS else 0

        for i in range(start_idx, len(all_rows)):
            row = all_rows[i]
            if row and row[0] == "出庫":
                display_rows.append(row)
                row_indices.append(i)

        # 新しいものを上にするため逆順に
        display_rows = display_rows[::-1]
        row_indices  = row_indices[::-1]

    return render_template(
        "log_out.html",
        title="出庫ログ",
        headers=LOG_HEADERS,
        rows=display_rows,
        row_indices=row_indices,
    )

from flask import request, render_template

@app.route("/print_tags/<base_name>")
def print_tags(base_name):
    """拠点別在庫から、指定された No. の行だけ値札を作って表示"""

    tag_type = request.args.get("type", "proper")  # 'proper' or 'event'
    nos_param = request.args.get("nos", "")        # "1001,1002,1003" みたいな文字列

    if not nos_param:
        return "値札対象の行が指定されていません。", 400

    target_nos = set(nos_param.split(","))

    # いつも使っている在庫読み込み関数
    rows = load_inventory(base_name)

    # CSV構成（拠点在庫）の想定：
    # [0] No.
    # [2] 地金
    # [5] サイズ
    # [6] 品番
    # [7] 上代
    # [8] 下代（暗号）
    # [9] 脇石（ct 表示用）
    # [14] 下代（数値） …あれば使う

    def to_int(val):
        try:
            return int(str(val).replace(",", ""))
        except Exception:
            return 0

    selected = [row for row in rows if str(row[0]) in target_nos]

    tags = []
    for row in selected:
        metal     = row[2]
        size_code = row[5]
        item_code = row[6]
        price_code = row[8]          # 暗号化下代
        ct_text   = row[9] or ""

        # 上代から税込/税抜きの表示価格を作る（ここはお好みで調整してOK）
        price_incl_num = to_int(row[7])          # 税込上代（仮）
        price_excl_num = int(round(price_incl_num / 1.1)) if price_incl_num else 0

        base_tag = {
            "metal": metal,
            "size_code": size_code,
            "item_code": item_code,
            "price_code_tax_incl": price_code,   # プロパー/催事の5行目・6行目で使う
            "price_code_tax_excl": price_code,
            "ct": ct_text,
        }

        if tag_type == "proper":
            tags.append({
                "type": "proper",
                **base_tag,
            })
        elif tag_type == "event":
            tags.append({
                "type": "event",
                **base_tag,
                "price_tax_incl": f"{price_incl_num:,}" if price_incl_num else "",
                "price_tax_excl": f"{price_excl_num:,}" if price_excl_num else "",
            })

    if not tags:
        return "値札対象がありません。", 400

    return render_template("price_tags.html", tags=tags)

# === アプリ起動 ===
if __name__ == "__main__":
    app.run(debug=True)
