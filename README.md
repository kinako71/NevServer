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

`--mock`を付けるとNI固有関数を呼ばずにモックデータを使います。実機モードではコマンド処理を共通に保ち、NI固有関数を呼ぶ箇所でNI APIを実行します。`GET DAQMXINFO` はC互換のSAO/DAO/SDOタスク情報に加え、NI実機では各デバイスの製品情報、シリアル番号、AI/AOチャンネル、端子を `DEVICE[...]` 行で返します。

`RUN DAO <index>` でAI (`PXI1Slot4/ai0`) とAOを同期して開始し、`GET AI <index>` で入力波形を取得、`STOP DAO <index>` で停止します。AOのサンプルクロックと開始トリガーには、提示された `/PXI1Slot4/te0/SampleClock` と `/PXI1Slot4/te0/StartTrigger` を使います。

実機用Python依存は次のコマンドで追加できます:

```bash
cd python
./.venv/bin/pip install -e '.[hardware]'
```

`SET PRC` / `RUN PRC` / `GET PRC` はPython版の追加コマンドで、PSA走査を行い各波形のpeak-to-peak値をパラメータに対して返します。

NI実機の動作確認手順は [NI_HARDWARE_TEST.md](NI_HARDWARE_TEST.md) を参照してください。
