# NI実機テスト用コマンドリスト

この手順は、Python版サーバーをNI実機モードで起動し、TCPシェルから
デバイス情報・DAQ出力/入力・NI-SCOPE計測を確認するためのものです。

## 実行前の確認

- NI-DAQmx、NI-SCOPE、NI-ModInstのドライバとPythonパッケージが使えること。
- NI-SCOPE機器とDAQ出力機器がNI MAXから見えること。
- タスク番号は実機の検出結果で決まります。`GET DAQMXINFO`を確認し、AO/AI
  の行に表示された0始まりの番号を使ってください。デバイス名やAOのサンプル
  クロック対応可否はNI-DAQmxから自動検出されます。
- 出力コマンドは実際に電圧を出します。まず外部回路を外すか安全な負荷に
  接続し、出力値を低く設定して確認してください。以下のAO例は0 Vです。

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
`SET AO`だけは、最初の応答でデータ転送モードに入った後に波形データを送ります。

## 1. 機器情報と初期状態を確認

```text
GET SYSINFO
GET DAQMXINFO
GET NISCOPEINFO
GET AO 0
GET DO 0
GET DI 0
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

## 2. AO出力と独立AIを確認

`GET DAQMXINFO`でAOタスクを選びます。周波数が0のタスクはオンデマンド出力
です。以下のサンプル周波数設定は、サンプルクロックに対応するAOタスクで行います。

```text
SET AO DLEN 0 8
SET AO FREQ 0 1000
SET AO 0 0
```

`SET AO 0 0`への応答が`#SETAO 0 #OK`になったら、続けて次のデータ行を送ります。

```text
[0 0 0 0 0 0 0 0]#OK
```

受信完了の`#OK`を確認したら、AOとAIをそれぞれ操作します。AOとAIのタスク番号は
別々に採番されるため、実機の`GET DAQMXINFO`に合わせてください。

```text
RUN AO 0
RUN AI 0
GET AI 0
STOP AI 0
STOP AO 0
GET AI 0
```

`GET AI 0`は1ブロックを返します。AIは`RUN AI`で明示開始でき、`GET AI`から
自動開始することもできます。`STOP AI`は停止前の最終ブロックを保持します。
停止後の`GET AI`がエラーになる場合は、その応答とサーバーログを記録してください。

## 3. NI-SCOPE単発計測（PSA）

以下は、NI-SCOPEのunion 0でチャンネル0/1を設定し、AOチャンネル0を
小さい範囲で掃引して各点の波形を取得する例です。入力信号と出力の配線を
確認してから実行してください。機器が2チャンネル未満の場合は`SET NSU CHAN`の
チャンネル設定を実機に合わせます。

```text
SET NSU DEVS 0 1 [ 0 ]
SET NSU CHAN 0 0 [ 1.0 AC 1.0 AC ]
SET NSU DLEN 0 1000
SET NSU FREQ 0 5000000
SET NSU TRIG 0 [ 50 IMMEDIATE IMMEDIATE 0 0 POSITIVE DC 0 0 ]
SET PSA 0 AO 0 0 -0.1 0.1 2 0
RUN PSA
```

`RUN PSA`はバックグラウンドで動くため、完了するまで状態を確認します。

```text
GET PSA NS STAT
GET PSA NS STAT
GET PSA NS DATA 0-
```

状態が`COMPLETE`になってから`GET PSA NS DATA 0-`を実行します。波形データが
複数行で返り、最後に`#OK`が付けば測定結果の取得まで完了です。計測を中断する
場合だけ、実行中に次を送ります。

```text
STOP PSA
```

## 4. DAQmx同期パルス計測（PULSE）

`SET PULSE`の後は、各行を送るたびにサーバーの`#OK`を待ちます。AOタスク番号は
`GET DAQMXINFO`の番号、AOチャンネル番号は物理チャンネル名の`ao`の後の数字です。
以下ではAOタスク0の`ao52`を使います。AIはAIタスク0の先頭チャンネルを使います。
`290 mV`の出力を配線前に確認し、接続先の許容電圧に合わせて振幅を下げてください。

```text
SET PULSE 0 52
100000 0.1
20 4 290
RUN PULSE
GET PULSE
```

1行目はサンプリング周波数(Hz)と計測時間(s)、2行目は遅延(ms)・パルス幅(ms)・
振幅(mV)です。各数値行の後に`#OK`が返り、設定が完了します。`RUN PULSE`でAIをAO
の開始トリガーに同期させて有限計測し、完了後の`#OK`を確認してから`GET PULSE`で
取得します。
結果は`#GETPULSE <サンプル数>`、設定値の行、続いて`時刻(s) AO電圧(V) AI電圧(V)`
の各サンプル行です。最後は`#OK`です。

取得結果はメモリに保持され、次回の`SET PULSE`で新しい設定に切り替わります。

## 5. NI-SCOPE計測とPRC集計（任意）

PSAと同じ掃引・取得をPRCコマンドで行い、各波形のpeak-to-peak値を取得します。

```text
SET PRC 0 AO 0 0 -0.1 0.1 2 0
RUN PRC
GET PRC
```

PRCもバックグラウンド計測です。`GET PRC`は計測完了後に実行してください。

## 失敗時に記録する情報

- 実行したコマンドと、その直後に返った応答全文
- サーバー起動時のエラーとトレースバック
- `GET DAQMXINFO`／`GET NISCOPEINFO`の結果
- NI MAXで表示される機器名、モデル名、端子ルート
