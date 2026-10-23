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

PSAの読み出しコマンドはNI-SCOPE用の`GET PSA NS DATA/STAT`とDAQmx用の`GET PSA DAQ DATA/STAT`に分かれています。DAQmx用の設定・実行コマンドは未実装のため、現時点ではDAQmx PSAのデータはまだ生成されません。

実機用Python依存は次のコマンドで追加できます:

```bash
cd python
./.venv/bin/pip install -e '.[hardware]'
```

`SET PRC <AOタスク> <AOチャンネル>`に続けて、最大位相時間(ms)・位相刻み(ms)・
繰り返し回数、その後にAIしきい値(mV)・パルス幅(ms)・基準AO値からの振幅増分(mV)を
指定します。`RUN PRC`では位相ごとに刺激なしの基準波形を取得した後、各試行の刺激波形を
取得します。しきい値交差から時刻を補間し、基準波形と刺激波形の時刻ゼロに最も近い発火から
3発火先の時間差を基準周期で割った値をPRCとして平均します。
`GET PRC STAT`で進行状態、`GET PRC DATA N|N-M|N-`で
位相ごとのPRC値を取得します。応答ヘッダーはそれぞれ`#PRCSTAT`と`#PRCDATA`です。

NI実機の動作確認手順は [NI_HARDWARE_TEST.md](NI_HARDWARE_TEST.md) を参照してください。
