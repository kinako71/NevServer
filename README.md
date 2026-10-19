# nev-server (Python)

## 実行（コマンドライン）

**初回だけ**（venv 作成 + インストール）:

```bash
cd python
./install
```

**サーバ起動**（C 版と同様ポート 9000）:

```bash
cd python
./nev-server --mock        # モックbackendで起動
./nev-server               # NI実機backendで起動
./nev-server --port 9000
./nev-server -h
```

PATH に venv を入れた場合は、どのディレクトリからでも:

```bash
export PATH="/path/to/R1.3a_20171012/python/.venv/bin:$PATH"
nev-server --mock
```

C 版 `nev_server` と同じく、クライアントは `localhost:9000` に TCP 接続します。

## 構成（C との対応）

| Python           | C                              |
| ---------------- | ------------------------------ |
| `nev.tcps`       | `nev_tcps.c`                   |
| `nev.shell`      | `nev_shell.c`                  |
| `nev.handlers.*` | `*_sh.c` / `nev_server.c`      |
| `nev.metecore`   | `nev_metecore.c`               |
| `nev.hardware`   | `nev_daqmx.c`, `nev_niscope.c` |

`--mock`を付けるとNI固有関数を呼ばずにモックデータを使います。実機モードではコマンド処理を共通に保ち、NI固有関数を呼ぶ箇所でNI APIを実行します。`GET DAQMXINFO` はAO/AI/DO/DIタスク情報に加え、NI実機では各デバイスの製品情報、シリアル番号、チャンネル、端子を `DEVICE[...]` 行で返します。

シェルコマンドはbootstrap.pyが列挙した物理グループをそのまま使います。`GET AO/AI/DO/DI <index>`でタスク値を読み、`SET AO <index> <channel>`でAO波形、`SET DO <index> <values...>`でDO値を設定します。AOの長さとサンプル周波数は`SET AO DLEN/FREQ`、出力開始停止は`RUN/STOP AO`で操作します。AIは`RUN/STOP AI`で制御し、`GET AI`で1ブロックを読みます。各グループのタスク番号は`GET DAQMXINFO`に表示されます。

実機用Python依存は次のコマンドで追加できます:

```bash
cd python
./.venv/bin/pip install -e '.[hardware]'
```

`SET PRC` / `RUN PRC` / `GET PRC` はPython版の追加コマンドで、PSA走査を行い各波形のpeak-to-peak値をパラメータに対して返します。

NI実機の動作確認手順は [NI_HARDWARE_TEST.md](NI_HARDWARE_TEST.md) を参照してください。
