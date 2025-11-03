from flask import Flask, render_template, request, redirect, url_for
import csv
import os
from collections import defaultdict

app = Flask(__name__)

# === 設定値 ===
BASE_NAMES = ["神戸", "横浜", "大宮", "泉北", "千葉"]
DATA_DIR = "data"
LOG_FILE = os.path.join(DATA_DIR, "log.csv")

HEADERS = [
    "No.", "出庫", "地金", "アイテム", "中石", "サイズ", "品番",
    "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日", "下代（数値）"
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
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for i, row in enumerate(rows, start=1):
            row[0] = str(i)
            writer.writerow(row)


def append_log(row, mode):
    """出庫・入庫ログ記録"""
    log_row = [mode] + row
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(log_row)


def summarize_inventory(rows):
    cats = ["リング", "ペンダント", "チェーン", "その他"]
    summary = {c: {"count": 0, "上代": 0, "下代": 0} for c in cats}
    totals = {"count": 0, "上代": 0, "下代": 0}

    for row in rows:
        try:
            item = str(row[2])  # 「アイテム」列（例：リング枠など）
            上代 = float(str(row[6]).replace(",", "") or 0)
            下代 = float(str(row[7]).replace(",", "") or 0)
        except Exception:
            continue

        # ✅ 部分一致・ゆるいマッチング対応
        if any(k in item for k in ["リング", "RING", "Ring"]):
            cat = "リング"
        elif any(k in item for k in ["ペンダント", "PENDANT", "Pendant"]):
            cat = "ペンダント"
        elif any(k in item for k in ["チェーン", "CHAIN", "Chain"]):
            cat = "チェーン"
        else:
            cat = "その他"

        summary[cat]["count"] += 1
        summary[cat]["上代"] += 上代
        summary[cat]["下代"] += 下代

        totals["count"] += 1
        totals["上代"] += 上代
        totals["下代"] += 下代

    # 桁区切り整形
    for c in cats:
        summary[c]["上代"] = f"{summary[c]['上代']:,.0f}"
        summary[c]["下代"] = f"{summary[c]['下代']:,.0f}"
    totals["上代"] = f"{totals['上代']:,.0f}"
    totals["下代"] = f"{totals['下代']:,.0f}"

    return summary, totals



# === ルーティング ===

@app.route("/")
def home():
    return render_template("index.html", base_names=BASE_NAMES)


@app.route("/inventory/<base_name>", methods=["GET", "POST"])
def inventory(base_name):
    if base_name not in BASE_NAMES:
        return "拠点が見つかりません", 404

    rows = load_inventory(base_name)

    if request.method == "POST":
        checked = request.form.getlist("checkout")
        new_rows = []
        for i, row in enumerate(rows):
            if str(i) in checked:
                append_log(row, "出庫")
            else:
                new_rows.append(row)
        save_inventory(base_name, new_rows)
        rows = new_rows

    headers = ["No.", "地金", "アイテム", "中石", "サイズ", "品番", "上代",
               "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日"]

    summary, totals = summarize_inventory(rows)

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


@app.route("/inventory_all")
def inventory_all():
    """全拠点の在庫を統合して表示（No.・出庫を除外）"""
    all_rows = []

    for base in BASE_NAMES:
        rows = load_inventory(base)
        for row in rows:
            if len(row) < 15:
                continue
            # 「No.」「出庫」を除いた部分を取得（地金〜入庫日）
            cleaned_row = row[2:15]
            # 拠点名を先頭に追加
            full_row = [base] + cleaned_row
            all_rows.append(full_row)

    # ✅ 集計では「拠点」列を除外して summarize_inventory に渡す
    #   row[1:] = 地金 〜 下代（数値）
    #   summarize_inventory は アイテム=2列目、上代=7列目、下代=8列目 として動作しているのでOK
    rows_for_summary = [r[1:] for r in all_rows if len(r) > 8]
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
        base_names=BASE_NAMES
    )

@app.route("/add_stock", methods=["GET", "POST"])
def add_stock():
    if request.method == "POST":
        base = request.form["拠点"]
        if base not in BASE_NAMES:
            return "拠点が不正です", 400

        row = [""]
        row.append("")  # 出庫欄
        for key in HEADERS[2:-1]:
            row.append(request.form.get(key, ""))
        row.append(request.form.get("下代（数値）", ""))

        rows = load_inventory(base)
        rows.append(row)
        save_inventory(base, rows)
        append_log(row, "入庫")
        return redirect(url_for("inventory", base_name=base))
    return render_template("add_stock.html", base_names=BASE_NAMES)


@app.route("/log")
def log():
    if not os.path.exists(LOG_FILE):
        return render_template("log.html", rows=[])
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return render_template("log.html", rows=list(csv.reader(f)))


# === アプリ起動 ===
if __name__ == "__main__":
    app.run(debug=True)
