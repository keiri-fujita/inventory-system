from flask import Flask, render_template, request, redirect, url_for
import csv
import os

app = Flask(__name__)

# 拠点名（順番固定）
BASE_NAMES = ["神戸", "横浜", "大宮", "泉北", "千葉"]
DATA_DIR = "data"
LOG_FILE = os.path.join(DATA_DIR, "log.csv")

# 共通ヘッダー
HEADERS = ["No.", "出庫", "地金", "アイテム", "中石", "サイズ", "品番",
           "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日", "下代（数値）"]

# ファイル読み込み
def load_inventory(base_name):
    path = os.path.join(DATA_DIR, f"{base_name}.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.reader(f))[1:]  # ヘッダー除外

# ファイル書き出し
def save_inventory(base_name, rows):
    path = os.path.join(DATA_DIR, f"{base_name}.csv")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        for i, row in enumerate(rows, start=1):
            row[0] = str(i)  # No. を振り直す
            writer.writerow(row)

# ログ追加
def append_log(row, mode):
    log_row = [mode] + row
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(log_row)

# ===== ルート =====

@app.route('/')
def home():
    return render_template("index.html", base_names=BASE_NAMES)

@app.route('/inventory/<base_name>', methods=["GET", "POST"])
def inventory(base_name):
    if base_name not in BASE_NAMES:
        return "拠点が見つかりません", 404

    rows = load_inventory(base_name)

    if request.method == "POST":
        # 出庫対象の行番号リスト
        checked = request.form.getlist("checkout")
        new_rows = []
        for i, row in enumerate(rows):
            if str(i) in checked:
                append_log(row, "出庫")
            else:
                new_rows.append(row)
        save_inventory(base_name, new_rows)
        rows = new_rows  # 再表示用に更新

    # ✅ ここでヘッダーを定義
    headers = ["No.", "地金", "アイテム", "中石", "サイズ", "品番", "上代", "下代", "脇石", "チェーン長", "摘要", "入力者", "入庫日"]

    # ✅ 必ず関数の中で return すること
    return render_template("inventory.html", base_name=base_name, headers=headers, rows=rows,
    enumerate=enumerate)


@app.route('/inventory_all')
def inventory_all():
    all_rows = []
    for base in BASE_NAMES:
        for row in load_inventory(base):
            all_rows.append([base] + row)
    return render_template("inventory_all.html", rows=all_rows, headers=HEADERS)

@app.route('/add_stock', methods=["GET", "POST"])
def add_stock():
    if request.method == "POST":
        base = request.form["拠点"]
        if base not in BASE_NAMES:
            return "拠点が不正です", 400
        row = [""]
        row.append("")  # 出庫欄
        for key in HEADERS[2:-1]:  # 下代（数値）は除外
            row.append(request.form.get(key, ""))
        row.append(request.form.get("下代（数値）", ""))
        rows = load_inventory(base)
        rows.append(row)
        save_inventory(base, rows)
        append_log(row, "入庫")
        return redirect(url_for("inventory", base_name=base))
    return render_template("add_stock.html", base_names=BASE_NAMES)

@app.route('/log')
def log():
    if not os.path.exists(LOG_FILE):
        return render_template("log.html", rows=[])
    with open(LOG_FILE, newline='', encoding='utf-8') as f:
        return render_template("log.html", rows=list(csv.reader(f)))

# === アプリ起動 ===
if __name__ == '__main__':
    app.run(debug=True)
