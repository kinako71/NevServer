# NI実機テスト用コマンドリスト

この手順は、Python版サーバーをNI実機モードで起動し、TCPシェルから
デバイス情報・DAQ出力/入力・NI-SCOPE計測を確認するためのものです。

## 実行前の確認

- NI-DAQmx、NI-SCOPE、NI-ModInstのドライバとPythonパッケージが使えること。
- NI-SCOPE機器とDAQ出力機器がNI MAXから見えること。
- `RUN DAO`のAI/AO同期処理は、現状`PXI1Slot4/ai0`、
  `/PXI1Slot4/te0/SampleClock`、`/PXI1Slot4/te0/StartTrigger`を使います。
  実機のデバイス名や端子ルートが異なる場合は、先に
  `src/nev/hardware/bootstrap.py`の`run_daq()`を実機に合わせてください。
- 出力コマンドは実際に電圧を出します。まず外部回路を外すか安全な負荷に
  接続し、出力値を低く設定して確認してください。以下のDAO例は0 Vです。

## 起動と接続

サーバーを実機モードで起動します（`--mock`は付けません）。

```bash
cd python
./nev-server --port 9000
```

別のターミナルから接続します。

```bash
nc 127.0.0.1 9000
```

コマンドは**1行ずつ**送信し、応答を受け取ってから次を送ってください。
`SET DAO`だけは、最初の応答でデータ転送モードに入った後に波形データを送ります。

## 1. 機器情報と初期状態を確認

```text
GET SYSINFO
GET DAQMXINFO
GET NISCOPEINFO
GET SAO 0
GET SDO 0
GET NSU NUM
GET NSU DEVS 0
GET NSU CHAN 0
GET NSU DLEN 0
GET NSU FREQ 0
GET NSU TRIG 0
```

各コマンドが`#OK`で終わることを確認します。`GET DAQMXINFO`にはDAQmxデバイス、
`GET NISCOPEINFO`にはNI-SCOPE機器の名前・モデル・チャンネル数・スロット・
シャーシ・シリアル番号が表示されます。

## 2. DAO出力と同期AIを確認

短い波形にして、DAOのチャンネル0へゼロ電圧を設定します。

```text
SET DAO DLEN 0 8
SET DAO FREQ 0 1000
SET DAO 0 0
```

`SET DAO 0 0`への応答が`#SETDAO 0 #OK`になったら、続けて次のデータ行を送ります。

```text
[0 0 0 0 0 0 0 0]#OK
```

受信完了の`#OK`を確認したら、AI/AOを開始してAIを読みます。

```text
RUN DAO 0
GET AI 0
STOP DAO 0
GET AI 0
```

`GET AI 0`は実行中のサンプルを返し、停止後も最後のサンプルブロックを返す想定です。
停止後の`GET AI`で`#NG`になる場合は、その応答とサーバーログを記録してください。

## 3. NI-SCOPE単発計測（PSA）

以下は、NI-SCOPEのunion 0でチャンネル0/1を設定し、SAOチャンネル0を
小さい範囲で掃引して各点の波形を取得する例です。入力信号と出力の配線を
確認してから実行してください。機器が2チャンネル未満の場合は`SET NSU CHAN`の
チャンネル設定を実機に合わせます。

```text
SET NSU DEVS 0 1 [ 0 ]
SET NSU CHAN 0 0 [ 1.0 AC 1.0 AC ]
SET NSU DLEN 0 1000
SET NSU FREQ 0 5000000
SET NSU TRIG 0 [ 50 IMMEDIATE IMMEDIATE 0 0 POSITIVE DC 0 0 ]
SET PSA 0 SAO 0 0 -0.1 0.1 2 0
RUN PSA
```

`RUN PSA`はバックグラウンドで動くため、完了するまで状態を確認します。

```text
GET PSA STAT
GET PSA STAT
GET PSA DATA 0-
```

状態が`COMPLETE`になってから`GET PSA DATA 0-`を実行します。波形データが
複数行で返り、最後に`#OK`が付けば測定結果の取得まで完了です。計測を中断する
場合だけ、実行中に次を送ります。

```text
STOP PSA
```

## 4. NI-SCOPE計測とPRC集計（任意）

PSAと同じ掃引・取得をPRCコマンドで行い、各波形のpeak-to-peak値を取得します。

```text
SET PRC 0 SAO 0 0 -0.1 0.1 2 0
RUN PRC
GET PRC
```

PRCもバックグラウンド計測です。`GET PRC`は計測完了後に実行してください。

## 失敗時に記録する情報

- 実行したコマンドと、その直後に返った応答全文
- サーバー起動時のエラーとトレースバック
- `GET DAQMXINFO`／`GET NISCOPEINFO`の結果
- NI MAXで表示される機器名、モデル名、端子ルート
