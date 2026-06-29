---
name: books
description: 読書リストコレクション。ユーザーが「この本を追加して」「読みたい本を登録」「この本を読み終えた、★4」「読書中の本は?」「技術書で評価の高い本は?」などと言ったら使う。記録は data/books/items/<id>.json に1件1ファイルで保存し、ユーザーは /collections/books で schema.json からレンダリングされた表・カンバン・入力フォームを見る。CRUD は manageCollection（または JSON ファイルへの Read / Write / Edit）で行う。
---

# 読書リスト（スキーマ駆動コレクション）

積読・読書中・読了の本を記録するコレクション。`status`（読みたい／読書中／読了）で
カンバン表示でき、`done` トグルで読了に切り替えられる。

## レコードの形

- `id` — kebab-case slug、主キー（ファイル名、拡張子なし）
- `title` — タイトル、必須
- `author` / `year` / `genre`(enum) — 任意
- `status` — 読みたい／読書中／読了。`rating`(1〜5) と `finishedDate` は `status=読了` のときだけ表示
- `notes` — メモ

## やること

- **追加 / 更新** — `manageCollection` putItems（`mode: "create"` で追加、`mode: "merge"` で部分更新）。
- **一覧 / 参照** — `manageCollection` getItems。
- **削除** — レコードファイルを削除。

レジストリ配布のテスト用に作られた無難なサンプルコレクション。
