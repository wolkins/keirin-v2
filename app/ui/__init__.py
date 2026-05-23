"""ローカル Web UI（Streamlit）パッケージ。

helpers.py は Streamlit 非依存で単体テスト可能。
streamlit_app.py は `streamlit run app/ui/streamlit_app.py` で起動する。

このパッケージは optional dependency `ui` 経由でインストールされる:
    pip install -e ".[ui]"
"""
