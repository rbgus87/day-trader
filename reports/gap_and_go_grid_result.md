# Gap & Go 전략 그리드 서치
> 갭업(1~15%) + 첫 5분봉 양봉 + 몸통비율≥0.5 → 추세 지속 진입
> 생성: 2026-05-21 09:53
> OLD 구간: 2025-04-01 ~ 2026-04-10
> NEW 구간: 2026-04-11 ~ 2026-05-19
> 선정 기준: PF≥1.5  AND  거래≥20  AND  연속손실≤8  AND  NEW PF>1.0
> 고정값: gap_max=15% / body_ratio=0.5 / entry_deadline=09:30 / sl_pct(fixed)=2% / trail_pct=2% / volume_ratio=2.0

## 그리드 결과 — OLD (144조합)

| tag | gap_min_pct | entry_mode | sl_mode | tp_mode | use_volume | pf | pnl | trades | win_rate | max_consec_loss | avg_hold_min | tp_pct | sl_pct | trail_pct | fc_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gap1pct_close_fixed_2pct_trail_only_volY | 1% | close | fixed_2pct | trail_only | Y | 1.0552 | 44858 | 506 | 0.3083 | 16 | 61.9000 | 0.0000 | 9.2900 | 83.2000 | 7.5100 |
| gap1pct_close_prev_close_trail_only_volY | 1% | close | prev_close | trail_only | Y | 1.0530 | 43256 | 506 | 0.3241 | 16 | 61.8000 | 0.0000 | 0.7900 | 92.0900 | 7.1100 |
| gap1pct_close_fixed_2pct_trail_only_volN | 1% | close | fixed_2pct | trail_only | N | 1.0405 | 33844 | 524 | 0.3149 | 16 | 64.4000 | 0.0000 | 9.1600 | 82.8200 | 8.0200 |
| gap1pct_close_prev_close_trail_only_volN | 1% | close | prev_close | trail_only | N | 1.0367 | 30846 | 524 | 0.3302 | 16 | 64.3000 | 0.0000 | 0.7600 | 91.6000 | 7.6300 |
| gap1pct_high_break_first_bar_low_trail_only_volN | 1% | high_break | first_bar_low | trail_only | N | 1.0052 | 3015 | 337 | 0.3828 | 10 | 64.2000 | 0.0000 | 3.8600 | 88.7200 | 7.4200 |
| gap1pct_high_break_first_bar_low_trail_only_volY | 1% | high_break | first_bar_low | trail_only | Y | 1.0015 | 891 | 324 | 0.3765 | 19 | 62.8000 | 0.0000 | 2.7800 | 90.1200 | 7.1000 |
| gap1pct_high_break_prev_close_trail_only_volN | 1% | high_break | prev_close | trail_only | N | 0.9998 | -126 | 337 | 0.3858 | 10 | 66.8000 | 0.0000 | 0.0000 | 91.9900 | 8.0100 |
| gap1pct_high_break_prev_close_trail_only_volY | 1% | high_break | prev_close | trail_only | Y | 0.9978 | -1253 | 324 | 0.3796 | 19 | 64.5000 | 0.0000 | 0.0000 | 92.2800 | 7.7200 |
| gap1pct_close_prev_close_tp5_volN | 1% | close | prev_close | tp5 | N | 0.9741 | -32743 | 524 | 0.4828 | 13 | 220.4000 | 32.4400 | 21.3700 | 0.0000 | 46.1800 |
| gap2pct_close_fixed_2pct_trail_only_volN | 2% | close | fixed_2pct | trail_only | N | 0.9723 | -11517 | 261 | 0.2912 | 16 | 34.3000 | 0.0000 | 10.3400 | 86.2100 | 3.4500 |
| gap2pct_close_fixed_2pct_trail_only_volY | 2% | close | fixed_2pct | trail_only | Y | 0.9676 | -13347 | 257 | 0.2879 | 16 | 33.2000 | 0.0000 | 10.5100 | 86.3800 | 3.1100 |
| gap1pct_close_fixed_2pct_tp5_volN | 1% | close | fixed_2pct | tp5 | N | 0.9598 | -42202 | 524 | 0.3340 | 13 | 108.2000 | 20.6100 | 60.1100 | 0.0000 | 19.2700 |
| gap1pct_high_break_first_bar_low_tp5_volN | 1% | high_break | first_bar_low | tp5 | N | 0.9584 | -35096 | 337 | 0.5163 | 9 | 165.0000 | 35.9100 | 33.8300 | 0.0000 | 30.2700 |
| gap1pct_close_prev_close_tp5_volY | 1% | close | prev_close | tp5 | Y | 0.9583 | -51601 | 506 | 0.4802 | 14 | 219.5000 | 32.6100 | 21.1500 | 0.0000 | 46.2500 |
| gap1pct_high_break_fixed_2pct_trail_only_volN | 1% | high_break | fixed_2pct | trail_only | N | 0.9555 | -26802 | 337 | 0.3709 | 10 | 65.4000 | 0.0000 | 7.7200 | 84.2700 | 8.0100 |
| gap2pct_close_prev_close_trail_only_volN | 2% | close | prev_close | trail_only | N | 0.9538 | -19649 | 261 | 0.3065 | 16 | 36.0000 | 0.0000 | 0.0000 | 96.5500 | 3.4500 |
| gap1pct_high_break_fixed_2pct_trail_only_volY | 1% | high_break | fixed_2pct | trail_only | Y | 0.9532 | -27868 | 324 | 0.3642 | 19 | 63.1000 | 0.0000 | 7.7200 | 84.5700 | 7.7200 |
| gap1pct_close_first_bar_low_trail_only_volY | 1% | close | first_bar_low | trail_only | Y | 0.9510 | -42801 | 506 | 0.2945 | 25 | 47.6000 | 0.0000 | 14.0300 | 80.8300 | 5.1400 |
| gap1pct_close_fixed_2pct_tp5_volY | 1% | close | fixed_2pct | tp5 | Y | 0.9494 | -51888 | 506 | 0.3281 | 14 | 105.2000 | 20.5500 | 60.6700 | 0.0000 | 18.7700 |
| gap2pct_close_prev_close_trail_only_volY | 2% | close | prev_close | trail_only | Y | 0.9489 | -21478 | 257 | 0.3035 | 16 | 34.9000 | 0.0000 | 0.0000 | 96.8900 | 3.1100 |
| gap1pct_high_break_fixed_2pct_tp5_volN | 1% | high_break | fixed_2pct | tp5 | N | 0.9469 | -39177 | 337 | 0.3917 | 10 | 111.7000 | 26.1100 | 54.6000 | 0.0000 | 19.2900 |
| gap1pct_close_first_bar_low_trail_only_volN | 1% | close | first_bar_low | trail_only | N | 0.9422 | -51721 | 524 | 0.2996 | 18 | 49.2000 | 0.0000 | 15.0800 | 79.3900 | 5.5300 |
| gap2pct_close_first_bar_low_trail_only_volN | 2% | close | first_bar_low | trail_only | N | 0.9185 | -35061 | 261 | 0.2874 | 16 | 28.5000 | 0.0000 | 8.4300 | 88.8900 | 2.6800 |
| gap1pct_high_break_fixed_2pct_tp5_volY | 1% | high_break | fixed_2pct | tp5 | Y | 0.9163 | -61073 | 324 | 0.3858 | 19 | 108.8000 | 26.2300 | 54.9400 | 0.0000 | 18.8300 |
| gap1pct_high_break_first_bar_low_tp5_volY | 1% | high_break | first_bar_low | tp5 | Y | 0.9158 | -70824 | 324 | 0.5123 | 13 | 165.0000 | 36.1100 | 33.6400 | 0.0000 | 30.2500 |
| gap2pct_close_first_bar_low_trail_only_volY | 2% | close | first_bar_low | trail_only | Y | 0.9123 | -37289 | 257 | 0.2840 | 16 | 27.3000 | 0.0000 | 8.1700 | 89.4900 | 2.3300 |
| gap2pct_close_first_bar_low_tp5_volN | 2% | close | first_bar_low | tp5 | N | 0.8989 | -63909 | 261 | 0.4138 | 11 | 114.6000 | 31.4200 | 49.8100 | 0.0000 | 18.7700 |
| gap1pct_close_first_bar_low_tp5_volN | 1% | close | first_bar_low | tp5 | N | 0.8894 | -133968 | 524 | 0.3836 | 13 | 120.4000 | 26.7200 | 53.2400 | 0.0000 | 20.0400 |
| gap2pct_close_fixed_2pct_tp5_volN | 2% | close | fixed_2pct | tp5 | N | 0.8844 | -63160 | 261 | 0.3065 | 11 | 74.7000 | 21.4600 | 66.2800 | 0.0000 | 12.2600 |
| gap1pct_close_first_bar_low_tp5_volY | 1% | close | first_bar_low | tp5 | Y | 0.8769 | -146689 | 506 | 0.3814 | 16 | 120.0000 | 26.8800 | 53.1600 | 0.0000 | 19.9600 |
| gap2pct_close_first_bar_low_tp5_volY | 2% | close | first_bar_low | tp5 | Y | 0.8745 | -78792 | 257 | 0.4125 | 11 | 113.6000 | 31.5200 | 49.8100 | 0.0000 | 18.6800 |
| gap1pct_high_break_prev_close_tp5_volN | 1% | high_break | prev_close | tp5 | N | 0.8621 | -133057 | 337 | 0.5519 | 9 | 226.8000 | 37.3900 | 14.5400 | 0.0000 | 48.0700 |
| gap2pct_close_prev_close_tp5_volN | 2% | close | prev_close | tp5 | N | 0.8578 | -107399 | 261 | 0.5326 | 7 | 223.0000 | 39.8500 | 12.6400 | 0.0000 | 47.5100 |
| gap2pct_close_fixed_2pct_tp5_volY | 2% | close | fixed_2pct | tp5 | Y | 0.8568 | -77537 | 257 | 0.3035 | 11 | 73.0000 | 21.4000 | 66.5400 | 0.0000 | 12.0600 |
| gap1pct_high_break_prev_close_tp5_volY | 1% | high_break | prev_close | tp5 | Y | 0.8255 | -167789 | 324 | 0.5494 | 13 | 226.2000 | 37.6500 | 14.2000 | 0.0000 | 48.1500 |
| gap2pct_close_prev_close_tp5_volY | 2% | close | prev_close | tp5 | Y | 0.8223 | -134005 | 257 | 0.5292 | 7 | 223.0000 | 39.6900 | 12.4500 | 0.0000 | 47.8600 |
| gap2pct_high_break_first_bar_low_tp5_volN | 2% | high_break | first_bar_low | tp5 | N | 0.7507 | -116943 | 176 | 0.5455 | 7 | 155.6000 | 39.7700 | 30.6800 | 0.0000 | 29.5500 |
| gap1pct_high_break_first_bar_low_tp3_volN | 1% | high_break | first_bar_low | tp3 | N | 0.7307 | -217989 | 337 | 0.5697 | 9 | 115.7000 | 51.0400 | 31.7500 | 0.0000 | 17.2100 |
| gap1pct_high_break_fixed_2pct_tp3_volN | 1% | high_break | fixed_2pct | tp3 | N | 0.7147 | -204210 | 337 | 0.4421 | 10 | 79.5000 | 38.5800 | 49.8500 | 0.0000 | 11.5700 |
| gap2pct_close_fixed_2pct_tp3_volN | 2% | close | fixed_2pct | tp3 | N | 0.7145 | -149279 | 261 | 0.3525 | 9 | 47.5000 | 31.8000 | 62.0700 | 0.0000 | 6.1300 |
| gap1pct_close_prev_close_tp3_volN | 1% | close | prev_close | tp3 | N | 0.7141 | -347385 | 524 | 0.5172 | 11 | 179.6000 | 43.3200 | 19.8500 | 0.0000 | 36.8300 |
| gap1pct_close_fixed_2pct_tp3_volN | 1% | close | fixed_2pct | tp3 | N | 0.7077 | -299608 | 524 | 0.3645 | 13 | 81.7000 | 29.5800 | 57.4400 | 0.0000 | 12.9800 |
| gap1pct_close_prev_close_tp3_volY | 1% | close | prev_close | tp3 | Y | 0.7039 | -352854 | 506 | 0.5158 | 12 | 179.7000 | 43.2800 | 19.5700 | 0.0000 | 37.1500 |
| gap2pct_high_break_fixed_2pct_tp5_volN | 2% | high_break | fixed_2pct | tp5 | N | 0.7024 | -115268 | 176 | 0.3693 | 12 | 85.3000 | 26.1400 | 59.6600 | 0.0000 | 14.2000 |
| gap2pct_high_break_first_bar_low_trail_only_volN | 2% | high_break | first_bar_low | trail_only | N | 0.7017 | -86434 | 176 | 0.3636 | 12 | 38.6000 | 0.0000 | 1.1400 | 95.4500 | 3.4100 |
| gap1pct_close_fixed_2pct_tp3_volY | 1% | close | fixed_2pct | tp3 | Y | 0.7009 | -299454 | 506 | 0.3597 | 14 | 80.3000 | 29.2500 | 57.9100 | 0.0000 | 12.8500 |
| gap2pct_high_break_prev_close_trail_only_volN | 2% | high_break | prev_close | trail_only | N | 0.7009 | -86783 | 176 | 0.3636 | 12 | 39.1000 | 0.0000 | 0.0000 | 96.5900 | 3.4100 |
| gap2pct_close_first_bar_low_tp3_volN | 2% | close | first_bar_low | tp3 | N | 0.7004 | -181881 | 261 | 0.4559 | 7 | 76.6000 | 42.5300 | 47.5100 | 0.0000 | 9.9600 |
| gap1pct_high_break_first_bar_low_tp3_volY | 1% | high_break | first_bar_low | tp3 | Y | 0.6986 | -243101 | 324 | 0.5679 | 13 | 116.9000 | 50.6200 | 31.4800 | 0.0000 | 17.9000 |
| gap2pct_high_break_first_bar_low_tp5_volY | 2% | high_break | first_bar_low | tp5 | Y | 0.6970 | -142120 | 173 | 0.5376 | 7 | 153.2000 | 39.8800 | 31.2100 | 0.0000 | 28.9000 |
| gap2pct_close_fixed_2pct_tp3_volY | 2% | close | fixed_2pct | tp3 | Y | 0.6968 | -157117 | 257 | 0.3502 | 8 | 47.8000 | 31.5200 | 62.2600 | 0.0000 | 6.2300 |
| gap2pct_high_break_first_bar_low_trail_only_volY | 2% | high_break | first_bar_low | trail_only | Y | 0.6945 | -87360 | 173 | 0.3584 | 12 | 36.8000 | 0.0000 | 1.1600 | 95.9500 | 2.8900 |
| gap2pct_high_break_prev_close_trail_only_volY | 2% | high_break | prev_close | trail_only | Y | 0.6936 | -87709 | 173 | 0.3584 | 12 | 37.2000 | 0.0000 | 0.0000 | 97.1100 | 2.8900 |
| gap1pct_high_break_fixed_2pct_tp3_volY | 1% | high_break | fixed_2pct | tp3 | Y | 0.6904 | -219063 | 324 | 0.4383 | 19 | 78.4000 | 37.9600 | 50.0000 | 0.0000 | 12.0400 |
| gap2pct_close_first_bar_low_tp3_volY | 2% | close | first_bar_low | tp3 | Y | 0.6845 | -190224 | 257 | 0.4553 | 7 | 77.4000 | 42.4100 | 47.4700 | 0.0000 | 10.1200 |
| gap2pct_high_break_first_bar_low_tp3_volN | 2% | high_break | first_bar_low | tp3 | N | 0.6680 | -145450 | 176 | 0.6080 | 7 | 107.2000 | 56.8200 | 28.4100 | 0.0000 | 14.7700 |
| gap2pct_high_break_fixed_2pct_tp5_volY | 2% | high_break | fixed_2pct | tp5 | Y | 0.6662 | -127935 | 173 | 0.3642 | 12 | 82.5000 | 26.5900 | 60.1200 | 0.0000 | 13.2900 |
| gap2pct_close_prev_close_tp3_volN | 2% | close | prev_close | tp3 | N | 0.6606 | -244078 | 261 | 0.5709 | 7 | 178.1000 | 51.7200 | 10.7300 | 0.0000 | 37.5500 |
| gap1pct_high_break_prev_close_tp3_volN | 1% | high_break | prev_close | tp3 | N | 0.6480 | -329842 | 337 | 0.5994 | 9 | 170.9000 | 53.1200 | 13.6500 | 0.0000 | 33.2300 |
| gap2pct_high_break_fixed_2pct_trail_only_volN | 2% | high_break | fixed_2pct | trail_only | N | 0.6466 | -106112 | 176 | 0.3409 | 12 | 37.1000 | 0.0000 | 9.0900 | 87.5000 | 3.4100 |
| gap1pct_close_first_bar_low_tp3_volN | 1% | close | first_bar_low | tp3 | N | 0.6442 | -418652 | 524 | 0.4179 | 13 | 86.4000 | 36.4500 | 51.3400 | 0.0000 | 12.2100 |
| gap2pct_high_break_fixed_2pct_trail_only_volY | 2% | high_break | fixed_2pct | trail_only | Y | 0.6389 | -107038 | 173 | 0.3353 | 12 | 35.2000 | 0.0000 | 9.2500 | 87.8600 | 2.8900 |
| gap2pct_close_prev_close_tp3_volY | 2% | close | prev_close | tp3 | Y | 0.6372 | -260594 | 257 | 0.5681 | 7 | 179.8000 | 51.3600 | 10.5100 | 0.0000 | 38.1300 |
| gap1pct_close_first_bar_low_tp3_volY | 1% | close | first_bar_low | tp3 | Y | 0.6355 | -421535 | 506 | 0.4170 | 16 | 87.3000 | 36.3600 | 51.1900 | 0.0000 | 12.4500 |
| gap2pct_high_break_fixed_2pct_tp3_volN | 2% | high_break | fixed_2pct | tp3 | N | 0.6348 | -133883 | 176 | 0.4261 | 12 | 54.7000 | 39.2000 | 53.9800 | 0.0000 | 6.8200 |
| gap2pct_high_break_first_bar_low_tp3_volY | 2% | high_break | first_bar_low | tp3 | Y | 0.6278 | -163060 | 173 | 0.6012 | 7 | 106.4000 | 56.0700 | 28.9000 | 0.0000 | 15.0300 |
| gap1pct_high_break_prev_close_tp3_volY | 1% | high_break | prev_close | tp3 | Y | 0.6207 | -353956 | 324 | 0.5988 | 13 | 171.2000 | 52.7800 | 13.2700 | 0.0000 | 33.9500 |
| gap3pct_high_break_first_bar_low_tp5_volN | 3% | high_break | first_bar_low | tp5 | N | 0.6095 | -147783 | 107 | 0.5981 | 4 | 144.7000 | 42.9900 | 28.9700 | 0.0000 | 28.0400 |
| gap2pct_high_break_fixed_2pct_tp3_volY | 2% | high_break | fixed_2pct | tp3 | Y | 0.6068 | -142557 | 173 | 0.4220 | 12 | 53.8000 | 38.7300 | 54.3400 | 0.0000 | 6.9400 |
| gap2pct_high_break_prev_close_tp5_volN | 2% | high_break | prev_close | tp5 | N | 0.6000 | -240448 | 176 | 0.5852 | 7 | 225.9000 | 41.4800 | 8.5200 | 0.0000 | 50.0000 |
| gap2pct_high_break_prev_close_tp5_volY | 2% | high_break | prev_close | tp5 | Y | 0.5581 | -265625 | 173 | 0.5780 | 7 | 224.8000 | 41.6200 | 8.6700 | 0.0000 | 49.7100 |
| gap3pct_high_break_first_bar_low_tp5_volY | 3% | high_break | first_bar_low | tp5 | Y | 0.5468 | -171515 | 105 | 0.5905 | 4 | 142.5000 | 42.8600 | 29.5200 | 0.0000 | 27.6200 |
| gap5pct_high_break_first_bar_low_tp5_volN | 5% | high_break | first_bar_low | tp5 | N | 0.5429 | -107634 | 52 | 0.6731 | 5 | 136.7000 | 48.0800 | 30.7700 | 0.0000 | 21.1500 |
| gap3pct_close_prev_close_tp5_volN | 3% | close | prev_close | tp5 | N | 0.5388 | -281148 | 154 | 0.5584 | 7 | 218.9000 | 42.2100 | 7.7900 | 0.0000 | 50.0000 |
| gap3pct_high_break_first_bar_low_tp3_volN | 3% | high_break | first_bar_low | tp3 | N | 0.5337 | -170669 | 107 | 0.6636 | 4 | 95.4000 | 62.6200 | 25.2300 | 0.0000 | 12.1500 |
| gap3pct_close_first_bar_low_tp5_volN | 3% | close | first_bar_low | tp5 | N | 0.5263 | -237904 | 154 | 0.4156 | 8 | 104.5000 | 31.8200 | 49.3500 | 0.0000 | 18.8300 |
| gap2pct_high_break_prev_close_tp3_volN | 2% | high_break | prev_close | tp3 | N | 0.5183 | -277620 | 176 | 0.6364 | 7 | 168.4000 | 59.0900 | 8.5200 | 0.0000 | 32.3900 |
| gap3pct_high_break_fixed_2pct_tp5_volN | 3% | high_break | fixed_2pct | tp5 | N | 0.5075 | -155405 | 107 | 0.3551 | 10 | 67.6000 | 24.3000 | 64.4900 | 0.0000 | 11.2100 |
| gap3pct_close_fixed_2pct_tp5_volN | 3% | close | fixed_2pct | tp5 | N | 0.5008 | -208282 | 154 | 0.2987 | 13 | 54.0000 | 21.4300 | 68.1800 | 0.0000 | 10.3900 |
| gap3pct_close_prev_close_tp5_volY | 3% | close | prev_close | tp5 | Y | 0.4975 | -306324 | 152 | 0.5526 | 7 | 218.3000 | 42.1100 | 7.8900 | 0.0000 | 50.0000 |
| gap3pct_close_first_bar_low_tp5_volY | 3% | close | first_bar_low | tp5 | Y | 0.4968 | -250760 | 152 | 0.4145 | 8 | 103.5000 | 32.2400 | 49.3400 | 0.0000 | 18.4200 |
| gap5pct_high_break_fixed_2pct_tp5_volN | 5% | high_break | fixed_2pct | tp5 | N | 0.4967 | -86889 | 52 | 0.4038 | 8 | 74.6000 | 26.9200 | 59.6200 | 0.0000 | 13.4600 |
| gap3pct_high_break_first_bar_low_tp3_volY | 3% | high_break | first_bar_low | tp3 | Y | 0.4895 | -186854 | 105 | 0.6571 | 4 | 95.8000 | 61.9000 | 25.7100 | 0.0000 | 12.3800 |
| gap2pct_high_break_prev_close_tp3_volY | 2% | high_break | prev_close | tp3 | Y | 0.4878 | -295230 | 173 | 0.6301 | 7 | 168.8000 | 58.3800 | 8.6700 | 0.0000 | 32.9500 |
| gap3pct_high_break_fixed_2pct_tp3_volN | 3% | high_break | fixed_2pct | tp3 | N | 0.4651 | -166151 | 107 | 0.4206 | 10 | 36.8000 | 39.2500 | 57.9400 | 0.0000 | 2.8000 |
| gap3pct_high_break_fixed_2pct_tp5_volY | 3% | high_break | fixed_2pct | tp5 | Y | 0.4651 | -166627 | 105 | 0.3524 | 10 | 65.2000 | 24.7600 | 64.7600 | 0.0000 | 10.4800 |
| gap3pct_close_fixed_2pct_tp5_volY | 3% | close | fixed_2pct | tp5 | Y | 0.4650 | -221032 | 152 | 0.2961 | 13 | 52.3000 | 21.7100 | 68.4200 | 0.0000 | 9.8700 |
| gap3pct_high_break_prev_close_tp5_volN | 3% | high_break | prev_close | tp5 | N | 0.4618 | -274435 | 107 | 0.6355 | 4 | 221.4000 | 44.8600 | 3.7400 | 0.0000 | 51.4000 |
| gap3pct_close_fixed_2pct_tp3_volN | 3% | close | fixed_2pct | tp3 | N | 0.4448 | -229850 | 154 | 0.3182 | 13 | 32.7000 | 29.8700 | 66.2300 | 0.0000 | 3.9000 |
| gap5pct_high_break_first_bar_low_tp3_volN | 5% | high_break | first_bar_low | tp3 | N | 0.4433 | -130592 | 52 | 0.6923 | 5 | 69.6000 | 69.2300 | 28.8500 | 0.0000 | 1.9200 |
| gap5pct_high_break_first_bar_low_tp5_volY | 5% | high_break | first_bar_low | tp5 | Y | 0.4421 | -131366 | 50 | 0.6600 | 5 | 131.8000 | 48.0000 | 32.0000 | 0.0000 | 20.0000 |
| gap3pct_close_fixed_2pct_trail_only_volN | 3% | close | fixed_2pct | trail_only | N | 0.4391 | -179957 | 154 | 0.2792 | 14 | 21.6000 | 0.0000 | 9.7400 | 88.3100 | 1.9500 |
| gap3pct_close_first_bar_low_tp3_volN | 3% | close | first_bar_low | tp3 | N | 0.4375 | -279649 | 154 | 0.4351 | 7 | 69.9000 | 41.5600 | 48.7000 | 0.0000 | 9.7400 |
| gap3pct_high_break_fixed_2pct_tp3_volY | 3% | high_break | fixed_2pct | tp3 | Y | 0.4344 | -173401 | 105 | 0.4190 | 10 | 37.1000 | 39.0500 | 58.1000 | 0.0000 | 2.8600 |
| gap5pct_high_break_fixed_2pct_tp3_volN | 5% | high_break | fixed_2pct | tp3 | N | 0.4306 | -97848 | 52 | 0.4423 | 8 | 23.5000 | 44.2300 | 55.7700 | 0.0000 | 0.0000 |
| gap3pct_close_prev_close_trail_only_volN | 3% | close | prev_close | trail_only | N | 0.4300 | -187323 | 154 | 0.2922 | 14 | 22.2000 | 0.0000 | 0.0000 | 98.0500 | 1.9500 |
| gap3pct_close_fixed_2pct_trail_only_volY | 3% | close | fixed_2pct | trail_only | Y | 0.4300 | -180443 | 152 | 0.2763 | 14 | 21.5000 | 0.0000 | 9.8700 | 88.1600 | 1.9700 |
| gap3pct_close_prev_close_tp3_volN | 3% | close | prev_close | tp3 | N | 0.4233 | -350632 | 154 | 0.5714 | 7 | 176.1000 | 53.2500 | 7.7900 | 0.0000 | 38.9600 |
| gap3pct_close_fixed_2pct_tp3_volY | 3% | close | fixed_2pct | tp3 | Y | 0.4217 | -237085 | 152 | 0.3158 | 13 | 33.0000 | 29.6100 | 66.4500 | 0.0000 | 3.9500 |
| gap3pct_close_prev_close_trail_only_volY | 3% | close | prev_close | trail_only | Y | 0.4210 | -187809 | 152 | 0.2895 | 14 | 22.1000 | 0.0000 | 0.0000 | 98.0300 | 1.9700 |
| gap3pct_close_first_bar_low_tp3_volY | 3% | close | first_bar_low | tp3 | Y | 0.4182 | -286990 | 152 | 0.4342 | 7 | 70.7000 | 41.4500 | 48.6800 | 0.0000 | 9.8700 |
| gap5pct_high_break_fixed_2pct_tp5_volY | 5% | high_break | fixed_2pct | tp5 | Y | 0.4181 | -98111 | 50 | 0.4000 | 8 | 69.9000 | 28.0000 | 60.0000 | 0.0000 | 12.0000 |
| gap3pct_high_break_prev_close_tp5_volY | 3% | high_break | prev_close | tp5 | Y | 0.4152 | -298167 | 105 | 0.6286 | 4 | 220.7000 | 44.7600 | 3.8100 | 0.0000 | 51.4300 |
| gap5pct_close_prev_close_tp5_volN | 5% | close | prev_close | tp5 | N | 0.3998 | -246767 | 71 | 0.5915 | 7 | 205.9000 | 47.8900 | 7.0400 | 0.0000 | 45.0700 |
| gap3pct_close_prev_close_tp3_volY | 3% | close | prev_close | tp3 | Y | 0.3968 | -366744 | 152 | 0.5658 | 7 | 177.4000 | 52.6300 | 7.8900 | 0.0000 | 39.4700 |
| gap3pct_high_break_prev_close_tp3_volN | 3% | high_break | prev_close | tp3 | N | 0.3926 | -305868 | 107 | 0.6822 | 4 | 160.1000 | 64.4900 | 3.7400 | 0.0000 | 31.7800 |
| gap3pct_close_first_bar_low_trail_only_volN | 3% | close | first_bar_low | trail_only | N | 0.3842 | -209369 | 154 | 0.2662 | 16 | 14.7000 | 0.0000 | 5.1900 | 94.1600 | 0.6500 |
| gap5pct_close_first_bar_low_tp5_volN | 5% | close | first_bar_low | tp5 | N | 0.3815 | -190254 | 71 | 0.4225 | 8 | 96.9000 | 32.3900 | 53.5200 | 0.0000 | 14.0800 |
| gap3pct_close_first_bar_low_trail_only_volY | 3% | close | first_bar_low | trail_only | Y | 0.3749 | -209855 | 152 | 0.2632 | 15 | 14.6000 | 0.0000 | 5.2600 | 94.0800 | 0.6600 |
| gap5pct_high_break_first_bar_low_tp3_volY | 5% | high_break | first_bar_low | tp3 | Y | 0.3743 | -146777 | 50 | 0.6800 | 5 | 69.4000 | 68.0000 | 30.0000 | 0.0000 | 2.0000 |
| gap5pct_high_break_fixed_2pct_tp3_volY | 5% | high_break | fixed_2pct | tp3 | Y | 0.3737 | -105097 | 50 | 0.4400 | 8 | 23.7000 | 44.0000 | 56.0000 | 0.0000 | 0.0000 |
| gap3pct_high_break_prev_close_tp3_volY | 3% | high_break | prev_close | tp3 | Y | 0.3605 | -322053 | 105 | 0.6762 | 4 | 161.8000 | 63.8100 | 3.8100 | 0.0000 | 32.3800 |
| gap5pct_high_break_prev_close_tp5_volN | 5% | high_break | prev_close | tp5 | N | 0.3514 | -235999 | 52 | 0.6731 | 5 | 213.2000 | 48.0800 | 3.8500 | 0.0000 | 48.0800 |
| gap3pct_high_break_first_bar_low_trail_only_volN | 3% | high_break | first_bar_low | trail_only | N | 0.3417 | -166973 | 107 | 0.3458 | 11 | 24.2000 | 0.0000 | 0.0000 | 99.0700 | 0.9300 |
| gap3pct_high_break_prev_close_trail_only_volN | 3% | high_break | prev_close | trail_only | N | 0.3417 | -166973 | 107 | 0.3458 | 11 | 24.2000 | 0.0000 | 0.0000 | 99.0700 | 0.9300 |
| gap5pct_close_prev_close_tp5_volY | 5% | close | prev_close | tp5 | Y | 0.3386 | -271943 | 69 | 0.5797 | 7 | 204.3000 | 47.8300 | 7.2500 | 0.0000 | 44.9300 |
| gap3pct_high_break_first_bar_low_trail_only_volY | 3% | high_break | first_bar_low | trail_only | Y | 0.3337 | -166453 | 105 | 0.3429 | 11 | 23.9000 | 0.0000 | 0.0000 | 99.0500 | 0.9500 |
| gap3pct_high_break_prev_close_trail_only_volY | 3% | high_break | prev_close | trail_only | Y | 0.3337 | -166453 | 105 | 0.3429 | 11 | 23.9000 | 0.0000 | 0.0000 | 99.0500 | 0.9500 |
| gap5pct_close_first_bar_low_tp5_volY | 5% | close | first_bar_low | tp5 | Y | 0.3312 | -203109 | 69 | 0.4203 | 8 | 94.4000 | 33.3300 | 53.6200 | 0.0000 | 13.0400 |
| gap3pct_high_break_fixed_2pct_trail_only_volN | 3% | high_break | fixed_2pct | trail_only | N | 0.2936 | -186449 | 107 | 0.3084 | 11 | 21.5000 | 0.0000 | 9.3500 | 89.7200 | 0.9300 |
| gap5pct_close_first_bar_low_tp3_volN | 5% | close | first_bar_low | tp3 | N | 0.2933 | -216462 | 71 | 0.4366 | 8 | 56.1000 | 43.6600 | 53.5200 | 0.0000 | 2.8200 |
| gap5pct_high_break_prev_close_tp3_volN | 5% | high_break | prev_close | tp3 | N | 0.2864 | -259096 | 52 | 0.6923 | 5 | 141.8000 | 69.2300 | 3.8500 | 0.0000 | 26.9200 |
| gap5pct_close_prev_close_tp3_volN | 5% | close | prev_close | tp3 | N | 0.2862 | -292557 | 71 | 0.6056 | 7 | 155.7000 | 60.5600 | 7.0400 | 0.0000 | 32.3900 |
| gap5pct_high_break_prev_close_tp5_volY | 5% | high_break | prev_close | tp5 | Y | 0.2861 | -259731 | 50 | 0.6600 | 5 | 211.4000 | 48.0000 | 4.0000 | 0.0000 | 48.0000 |
| gap3pct_high_break_fixed_2pct_trail_only_volY | 3% | high_break | fixed_2pct | trail_only | Y | 0.2853 | -185929 | 105 | 0.3048 | 11 | 21.2000 | 0.0000 | 9.5200 | 89.5200 | 0.9500 |
| gap5pct_close_fixed_2pct_tp5_volN | 5% | close | fixed_2pct | tp5 | N | 0.2797 | -171096 | 71 | 0.2394 | 16 | 37.8000 | 16.9000 | 76.0600 | 0.0000 | 7.0400 |
| gap5pct_close_first_bar_low_tp3_volY | 5% | close | first_bar_low | tp3 | Y | 0.2599 | -223803 | 69 | 0.4348 | 8 | 57.5000 | 43.4800 | 53.6200 | 0.0000 | 2.9000 |
| gap5pct_close_prev_close_tp3_volY | 5% | close | prev_close | tp3 | Y | 0.2469 | -308668 | 69 | 0.5942 | 7 | 157.9000 | 59.4200 | 7.2500 | 0.0000 | 33.3300 |
| gap5pct_high_break_prev_close_tp3_volY | 5% | high_break | prev_close | tp3 | Y | 0.2419 | -275281 | 50 | 0.6800 | 5 | 144.5000 | 68.0000 | 4.0000 | 0.0000 | 28.0000 |
| gap5pct_close_fixed_2pct_tp3_volN | 5% | close | fixed_2pct | tp3 | N | 0.2415 | -179348 | 71 | 0.2535 | 16 | 15.1000 | 25.3500 | 74.6500 | 0.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_tp5_volY | 5% | close | fixed_2pct | tp5 | Y | 0.2128 | -183845 | 69 | 0.2319 | 15 | 33.6000 | 17.3900 | 76.8100 | 0.0000 | 5.8000 |
| gap5pct_close_fixed_2pct_tp3_volY | 5% | close | fixed_2pct | tp3 | Y | 0.1973 | -186584 | 69 | 0.2464 | 15 | 15.4000 | 24.6400 | 75.3600 | 0.0000 | 0.0000 |
| gap5pct_high_break_first_bar_low_trail_only_volN | 5% | high_break | first_bar_low | trail_only | N | 0.1658 | -133365 | 52 | 0.3846 | 7 | 18.5000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_prev_close_trail_only_volN | 5% | high_break | prev_close | trail_only | N | 0.1658 | -133365 | 52 | 0.3846 | 7 | 18.5000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_first_bar_low_trail_only_volY | 5% | high_break | first_bar_low | trail_only | Y | 0.1487 | -132845 | 50 | 0.3800 | 7 | 17.7000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_prev_close_trail_only_volY | 5% | high_break | prev_close | trail_only | Y | 0.1487 | -132845 | 50 | 0.3800 | 7 | 17.7000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_prev_close_trail_only_volN | 5% | close | prev_close | trail_only | N | 0.1324 | -188456 | 71 | 0.2394 | 13 | 9.9000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_trail_only_volN | 5% | close | fixed_2pct | trail_only | N | 0.1322 | -185946 | 71 | 0.2113 | 16 | 9.5000 | 0.0000 | 11.2700 | 88.7300 | 0.0000 |
| gap5pct_close_first_bar_low_trail_only_volN | 5% | close | first_bar_low | trail_only | N | 0.1318 | -188007 | 71 | 0.2254 | 13 | 9.6000 | 0.0000 | 4.2300 | 95.7700 | 0.0000 |
| gap5pct_high_break_fixed_2pct_trail_only_volN | 5% | high_break | fixed_2pct | trail_only | N | 0.1252 | -142033 | 52 | 0.3654 | 10 | 18.2000 | 0.0000 | 3.8500 | 96.1500 | 0.0000 |
| gap5pct_close_prev_close_trail_only_volY | 5% | close | prev_close | trail_only | Y | 0.1126 | -188942 | 69 | 0.2319 | 12 | 9.3000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_trail_only_volY | 5% | close | fixed_2pct | trail_only | Y | 0.1120 | -186432 | 69 | 0.2029 | 15 | 8.9000 | 0.0000 | 11.5900 | 88.4100 | 0.0000 |
| gap5pct_close_first_bar_low_trail_only_volY | 5% | close | first_bar_low | trail_only | Y | 0.1119 | -188493 | 69 | 0.2174 | 12 | 9.0000 | 0.0000 | 4.3500 | 95.6500 | 0.0000 |
| gap5pct_high_break_fixed_2pct_trail_only_volY | 5% | high_break | fixed_2pct | trail_only | Y | 0.1075 | -141513 | 50 | 0.3600 | 10 | 17.3000 | 0.0000 | 4.0000 | 96.0000 | 0.0000 |

## 그리드 결과 — NEW (144조합)

| tag | gap_min_pct | entry_mode | sl_mode | tp_mode | use_volume | pf | pnl | trades | win_rate | max_consec_loss | avg_hold_min | tp_pct | sl_pct | trail_pct | fc_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gap3pct_close_fixed_2pct_tp3_volY | 3% | close | fixed_2pct | tp3 | Y | 5.0026 | 39563 | 12 | 0.6667 | 3 | 15.2000 | 66.6700 | 33.3300 | 0.0000 | 0.0000 |
| gap3pct_close_fixed_2pct_tp3_volN | 3% | close | fixed_2pct | tp3 | N | 5.0026 | 39563 | 12 | 0.6667 | 3 | 15.2000 | 66.6700 | 33.3300 | 0.0000 | 0.0000 |
| gap3pct_close_first_bar_low_trail_only_volY | 3% | close | first_bar_low | trail_only | Y | 4.9926 | 30144 | 12 | 0.7500 | 3 | 38.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_close_first_bar_low_trail_only_volN | 3% | close | first_bar_low | trail_only | N | 4.9926 | 30144 | 12 | 0.7500 | 3 | 38.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_close_prev_close_trail_only_volY | 3% | close | prev_close | trail_only | Y | 4.9926 | 30144 | 12 | 0.7500 | 3 | 38.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_close_prev_close_trail_only_volN | 3% | close | prev_close | trail_only | N | 4.9926 | 30144 | 12 | 0.7500 | 3 | 38.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap2pct_close_prev_close_trail_only_volY | 2% | close | prev_close | trail_only | Y | 4.5650 | 65942 | 28 | 0.5000 | 4 | 22.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap2pct_close_prev_close_trail_only_volN | 2% | close | prev_close | trail_only | N | 4.5650 | 65942 | 28 | 0.5000 | 4 | 22.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap3pct_close_prev_close_tp5_volY | 3% | close | prev_close | tp5 | Y | 4.0784 | 57316 | 12 | 0.5833 | 5 | 181.4000 | 58.3300 | 0.0000 | 0.0000 | 41.6700 |
| gap3pct_close_prev_close_tp5_volN | 3% | close | prev_close | tp5 | N | 4.0784 | 57316 | 12 | 0.5833 | 5 | 181.4000 | 58.3300 | 0.0000 | 0.0000 | 41.6700 |
| gap3pct_close_fixed_2pct_tp5_volY | 3% | close | fixed_2pct | tp5 | Y | 4.0419 | 47716 | 12 | 0.4167 | 6 | 89.7000 | 41.6700 | 41.6700 | 0.0000 | 16.6700 |
| gap3pct_close_fixed_2pct_tp5_volN | 3% | close | fixed_2pct | tp5 | N | 4.0419 | 47716 | 12 | 0.4167 | 6 | 89.7000 | 41.6700 | 41.6700 | 0.0000 | 16.6700 |
| gap2pct_close_fixed_2pct_trail_only_volY | 2% | close | fixed_2pct | trail_only | Y | 3.9666 | 61738 | 28 | 0.4643 | 4 | 22.4000 | 0.0000 | 7.1400 | 92.8600 | 0.0000 ✓ |
| gap2pct_close_fixed_2pct_trail_only_volN | 2% | close | fixed_2pct | trail_only | N | 3.9666 | 61738 | 28 | 0.4643 | 4 | 22.4000 | 0.0000 | 7.1400 | 92.8600 | 0.0000 ✓ |
| gap2pct_close_first_bar_low_trail_only_volY | 2% | close | first_bar_low | trail_only | Y | 3.8401 | 62451 | 28 | 0.5000 | 4 | 22.4000 | 0.0000 | 3.5700 | 96.4300 | 0.0000 ✓ |
| gap2pct_close_first_bar_low_trail_only_volN | 2% | close | first_bar_low | trail_only | N | 3.8401 | 62451 | 28 | 0.5000 | 4 | 22.4000 | 0.0000 | 3.5700 | 96.4300 | 0.0000 ✓ |
| gap3pct_close_fixed_2pct_trail_only_volY | 3% | close | fixed_2pct | trail_only | Y | 3.6106 | 25887 | 12 | 0.6667 | 3 | 37.6000 | 0.0000 | 8.3300 | 91.6700 | 0.0000 |
| gap3pct_close_fixed_2pct_trail_only_volN | 3% | close | fixed_2pct | trail_only | N | 3.6106 | 25887 | 12 | 0.6667 | 3 | 37.6000 | 0.0000 | 8.3300 | 91.6700 | 0.0000 |
| gap3pct_close_prev_close_tp3_volY | 3% | close | prev_close | tp3 | Y | 3.4354 | 38339 | 12 | 0.8333 | 2 | 82.4000 | 83.3300 | 0.0000 | 0.0000 | 16.6700 |
| gap3pct_close_prev_close_tp3_volN | 3% | close | prev_close | tp3 | N | 3.4354 | 38339 | 12 | 0.8333 | 2 | 82.4000 | 83.3300 | 0.0000 | 0.0000 | 16.6700 |
| gap3pct_close_first_bar_low_tp5_volY | 3% | close | first_bar_low | tp5 | Y | 3.3885 | 53525 | 12 | 0.5833 | 5 | 131.6000 | 58.3300 | 16.6700 | 0.0000 | 25.0000 |
| gap3pct_close_first_bar_low_tp5_volN | 3% | close | first_bar_low | tp5 | N | 3.3885 | 53525 | 12 | 0.5833 | 5 | 131.6000 | 58.3300 | 16.6700 | 0.0000 | 25.0000 |
| gap2pct_close_fixed_2pct_tp5_volY | 2% | close | fixed_2pct | tp5 | Y | 3.0928 | 75210 | 28 | 0.3571 | 6 | 52.3000 | 35.7100 | 57.1400 | 0.0000 | 7.1400 ✓ |
| gap2pct_close_fixed_2pct_tp5_volN | 2% | close | fixed_2pct | tp5 | N | 3.0928 | 75210 | 28 | 0.3571 | 6 | 52.3000 | 35.7100 | 57.1400 | 0.0000 | 7.1400 ✓ |
| gap2pct_close_first_bar_low_tp5_volY | 2% | close | first_bar_low | tp5 | Y | 3.0803 | 87839 | 28 | 0.5714 | 3 | 109.3000 | 50.0000 | 32.1400 | 0.0000 | 17.8600 ✓ |
| gap2pct_close_first_bar_low_tp5_volN | 2% | close | first_bar_low | tp5 | N | 3.0803 | 87839 | 28 | 0.5714 | 3 | 109.3000 | 50.0000 | 32.1400 | 0.0000 | 17.8600 ✓ |
| gap2pct_close_prev_close_tp5_volY | 2% | close | prev_close | tp5 | Y | 2.9607 | 86133 | 28 | 0.5714 | 3 | 165.9000 | 50.0000 | 17.8600 | 0.0000 | 32.1400 ✓ |
| gap2pct_close_prev_close_tp5_volN | 2% | close | prev_close | tp5 | N | 2.9607 | 86133 | 28 | 0.5714 | 3 | 165.9000 | 50.0000 | 17.8600 | 0.0000 | 32.1400 ✓ |
| gap3pct_close_first_bar_low_tp3_volY | 3% | close | first_bar_low | tp3 | Y | 2.7687 | 34548 | 12 | 0.8333 | 2 | 32.6000 | 83.3300 | 16.6700 | 0.0000 | 0.0000 |
| gap3pct_close_first_bar_low_tp3_volN | 3% | close | first_bar_low | tp3 | N | 2.7687 | 34548 | 12 | 0.8333 | 2 | 32.6000 | 83.3300 | 16.6700 | 0.0000 | 0.0000 |
| gap2pct_close_fixed_2pct_tp3_volY | 2% | close | fixed_2pct | tp3 | Y | 2.5566 | 46909 | 28 | 0.4643 | 5 | 19.3000 | 46.4300 | 53.5700 | 0.0000 | 0.0000 ✓ |
| gap2pct_close_fixed_2pct_tp3_volN | 2% | close | fixed_2pct | tp3 | N | 2.5566 | 46909 | 28 | 0.4643 | 5 | 19.3000 | 46.4300 | 53.5700 | 0.0000 | 0.0000 ✓ |
| gap3pct_high_break_first_bar_low_tp3_volY | 3% | high_break | first_bar_low | tp3 | Y | 2.5233 | 17649 | 9 | 0.7778 | 2 | 55.0000 | 77.7800 | 11.1100 | 0.0000 | 11.1100 |
| gap3pct_high_break_first_bar_low_tp3_volN | 3% | high_break | first_bar_low | tp3 | N | 2.5233 | 17649 | 9 | 0.7778 | 2 | 55.0000 | 77.7800 | 11.1100 | 0.0000 | 11.1100 |
| gap3pct_high_break_first_bar_low_tp5_volY | 3% | high_break | first_bar_low | tp5 | Y | 2.4103 | 23563 | 9 | 0.6667 | 3 | 124.1000 | 66.6700 | 11.1100 | 0.0000 | 22.2200 |
| gap3pct_high_break_first_bar_low_tp5_volN | 3% | high_break | first_bar_low | tp5 | N | 2.4103 | 23563 | 9 | 0.6667 | 3 | 124.1000 | 66.6700 | 11.1100 | 0.0000 | 22.2200 |
| gap2pct_close_prev_close_tp3_volY | 2% | close | prev_close | tp3 | Y | 2.3288 | 50369 | 28 | 0.7143 | 2 | 101.2000 | 71.4300 | 14.2900 | 0.0000 | 14.2900 ✓ |
| gap2pct_close_prev_close_tp3_volN | 2% | close | prev_close | tp3 | N | 2.3288 | 50369 | 28 | 0.7143 | 2 | 101.2000 | 71.4300 | 14.2900 | 0.0000 | 14.2900 ✓ |
| gap2pct_high_break_first_bar_low_tp5_volY | 2% | high_break | first_bar_low | tp5 | Y | 2.3067 | 49473 | 21 | 0.5714 | 3 | 104.5000 | 52.3800 | 28.5700 | 0.0000 | 19.0500 ✓ |
| gap2pct_high_break_first_bar_low_tp5_volN | 2% | high_break | first_bar_low | tp5 | N | 2.3067 | 49473 | 21 | 0.5714 | 3 | 104.5000 | 52.3800 | 28.5700 | 0.0000 | 19.0500 ✓ |
| gap5pct_close_first_bar_low_trail_only_volY | 5% | close | first_bar_low | trail_only | Y | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_first_bar_low_trail_only_volN | 5% | close | first_bar_low | trail_only | N | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_prev_close_trail_only_volY | 5% | close | prev_close | trail_only | Y | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_prev_close_trail_only_volN | 5% | close | prev_close | trail_only | N | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_trail_only_volY | 5% | close | fixed_2pct | trail_only | Y | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_trail_only_volN | 5% | close | fixed_2pct | trail_only | N | 2.2992 | 2485 | 4 | 0.5000 | 2 | 5.8000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap2pct_close_first_bar_low_tp3_volY | 2% | close | first_bar_low | tp3 | Y | 2.2065 | 47474 | 28 | 0.6786 | 2 | 45.2000 | 67.8600 | 32.1400 | 0.0000 | 0.0000 ✓ |
| gap2pct_close_first_bar_low_tp3_volN | 2% | close | first_bar_low | tp3 | N | 2.2065 | 47474 | 28 | 0.6786 | 2 | 45.2000 | 67.8600 | 32.1400 | 0.0000 | 0.0000 ✓ |
| gap1pct_close_prev_close_trail_only_volY | 1% | close | prev_close | trail_only | Y | 2.1905 | 70070 | 51 | 0.4510 | 6 | 38.3000 | 0.0000 | 0.0000 | 98.0400 | 1.9600 ✓ |
| gap1pct_close_prev_close_trail_only_volN | 1% | close | prev_close | trail_only | N | 2.1726 | 69583 | 52 | 0.4423 | 6 | 37.9000 | 0.0000 | 0.0000 | 98.0800 | 1.9200 ✓ |
| gap5pct_close_fixed_2pct_tp3_volY | 5% | close | fixed_2pct | tp3 | Y | 2.1381 | 5457 | 4 | 0.5000 | 2 | 8.5000 | 50.0000 | 50.0000 | 0.0000 | 0.0000 |
| gap5pct_close_fixed_2pct_tp3_volN | 5% | close | fixed_2pct | tp3 | N | 2.1381 | 5457 | 4 | 0.5000 | 2 | 8.5000 | 50.0000 | 50.0000 | 0.0000 | 0.0000 |
| gap1pct_close_fixed_2pct_trail_only_volY | 1% | close | fixed_2pct | trail_only | Y | 2.1032 | 66633 | 51 | 0.4314 | 6 | 37.8000 | 0.0000 | 7.8400 | 90.2000 | 1.9600 ✓ |
| gap3pct_high_break_prev_close_tp5_volY | 3% | high_break | prev_close | tp5 | Y | 2.0865 | 20970 | 9 | 0.6667 | 3 | 161.3000 | 66.6700 | 0.0000 | 0.0000 | 33.3300 |
| gap3pct_high_break_prev_close_tp5_volN | 3% | high_break | prev_close | tp5 | N | 2.0865 | 20970 | 9 | 0.6667 | 3 | 161.3000 | 66.6700 | 0.0000 | 0.0000 | 33.3300 |
| gap1pct_close_fixed_2pct_trail_only_volN | 1% | close | fixed_2pct | trail_only | N | 2.0863 | 66146 | 52 | 0.4231 | 6 | 37.3000 | 0.0000 | 7.6900 | 90.3800 | 1.9200 ✓ |
| gap3pct_high_break_prev_close_tp3_volY | 3% | high_break | prev_close | tp3 | Y | 2.0617 | 15056 | 9 | 0.7778 | 2 | 92.2000 | 77.7800 | 0.0000 | 0.0000 | 22.2200 |
| gap3pct_high_break_prev_close_tp3_volN | 3% | high_break | prev_close | tp3 | N | 2.0617 | 15056 | 9 | 0.7778 | 2 | 92.2000 | 77.7800 | 0.0000 | 0.0000 | 22.2200 |
| gap2pct_high_break_prev_close_tp5_volY | 2% | high_break | prev_close | tp5 | Y | 1.8806 | 40894 | 21 | 0.5714 | 3 | 155.5000 | 52.3800 | 19.0500 | 0.0000 | 28.5700 ✓ |
| gap2pct_high_break_prev_close_tp5_volN | 2% | high_break | prev_close | tp5 | N | 1.8806 | 40894 | 21 | 0.5714 | 3 | 155.5000 | 52.3800 | 19.0500 | 0.0000 | 28.5700 ✓ |
| gap5pct_close_first_bar_low_tp3_volY | 5% | close | first_bar_low | tp3 | Y | 1.8173 | 5405 | 4 | 0.7500 | 1 | 34.2000 | 75.0000 | 25.0000 | 0.0000 | 0.0000 |
| gap5pct_close_first_bar_low_tp3_volN | 5% | close | first_bar_low | tp3 | N | 1.8173 | 5405 | 4 | 0.7500 | 1 | 34.2000 | 75.0000 | 25.0000 | 0.0000 | 0.0000 |
| gap2pct_high_break_first_bar_low_tp3_volY | 2% | high_break | first_bar_low | tp3 | Y | 1.8147 | 26572 | 21 | 0.6667 | 2 | 45.2000 | 66.6700 | 28.5700 | 0.0000 | 4.7600 ✓ |
| gap2pct_high_break_first_bar_low_tp3_volN | 2% | high_break | first_bar_low | tp3 | N | 1.8147 | 26572 | 21 | 0.6667 | 2 | 45.2000 | 66.6700 | 28.5700 | 0.0000 | 4.7600 ✓ |
| gap2pct_high_break_fixed_2pct_tp5_volY | 2% | high_break | fixed_2pct | tp5 | Y | 1.6815 | 25798 | 21 | 0.4286 | 4 | 45.6000 | 42.8600 | 57.1400 | 0.0000 | 0.0000 ✓ |
| gap2pct_high_break_fixed_2pct_tp5_volN | 2% | high_break | fixed_2pct | tp5 | N | 1.6815 | 25798 | 21 | 0.4286 | 4 | 45.6000 | 42.8600 | 57.1400 | 0.0000 | 0.0000 ✓ |
| gap1pct_close_first_bar_low_trail_only_volY | 1% | close | first_bar_low | trail_only | Y | 1.6253 | 46328 | 51 | 0.4118 | 6 | 31.5000 | 0.0000 | 9.8000 | 88.2400 | 1.9600 ✓ |
| gap1pct_close_first_bar_low_trail_only_volN | 1% | close | first_bar_low | trail_only | N | 1.6147 | 45841 | 52 | 0.4038 | 6 | 31.2000 | 0.0000 | 9.6200 | 88.4600 | 1.9200 ✓ |
| gap1pct_close_fixed_2pct_tp5_volY | 1% | close | fixed_2pct | tp5 | Y | 1.5976 | 57166 | 51 | 0.3725 | 8 | 69.6000 | 37.2500 | 54.9000 | 0.0000 | 7.8400 ✓ |
| gap2pct_high_break_first_bar_low_trail_only_volY | 2% | high_break | first_bar_low | trail_only | Y | 1.5941 | 15232 | 21 | 0.4762 | 4 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap2pct_high_break_first_bar_low_trail_only_volN | 2% | high_break | first_bar_low | trail_only | N | 1.5941 | 15232 | 21 | 0.4762 | 4 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap2pct_high_break_prev_close_trail_only_volY | 2% | high_break | prev_close | trail_only | Y | 1.5941 | 15232 | 21 | 0.4762 | 4 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap2pct_high_break_prev_close_trail_only_volN | 2% | high_break | prev_close | trail_only | N | 1.5941 | 15232 | 21 | 0.4762 | 4 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 ✓ |
| gap2pct_high_break_fixed_2pct_trail_only_volY | 2% | high_break | fixed_2pct | trail_only | Y | 1.5842 | 15073 | 21 | 0.4762 | 4 | 12.1000 | 0.0000 | 9.5200 | 90.4800 | 0.0000 ✓ |
| gap2pct_high_break_fixed_2pct_trail_only_volN | 2% | high_break | fixed_2pct | trail_only | N | 1.5842 | 15073 | 21 | 0.4762 | 4 | 12.1000 | 0.0000 | 9.5200 | 90.4800 | 0.0000 ✓ |
| gap1pct_high_break_first_bar_low_tp5_volY | 1% | high_break | first_bar_low | tp5 | Y | 1.5506 | 43537 | 39 | 0.5128 | 5 | 117.2000 | 46.1500 | 33.3300 | 0.0000 | 20.5100 ✓ |
| gap1pct_close_fixed_2pct_tp5_volN | 1% | close | fixed_2pct | tp5 | N | 1.5367 | 53374 | 52 | 0.3654 | 8 | 70.9000 | 36.5400 | 55.7700 | 0.0000 | 7.6900 ✓ |
| gap1pct_close_first_bar_low_tp5_volY | 1% | close | first_bar_low | tp5 | Y | 1.4877 | 54984 | 51 | 0.4902 | 5 | 101.8000 | 45.1000 | 41.1800 | 0.0000 | 13.7300 |
| gap1pct_high_break_prev_close_tp5_volY | 1% | high_break | prev_close | tp5 | Y | 1.4823 | 41388 | 39 | 0.5385 | 5 | 155.3000 | 48.7200 | 23.0800 | 0.0000 | 28.2100 |
| gap1pct_high_break_first_bar_low_tp5_volN | 1% | high_break | first_bar_low | tp5 | N | 1.4762 | 39554 | 40 | 0.5000 | 5 | 116.1000 | 45.0000 | 35.0000 | 0.0000 | 20.0000 |
| gap1pct_close_first_bar_low_tp5_volN | 1% | close | first_bar_low | tp5 | N | 1.4569 | 52602 | 52 | 0.4808 | 5 | 101.3000 | 44.2300 | 42.3100 | 0.0000 | 13.4600 |
| gap1pct_high_break_fixed_2pct_tp5_volY | 1% | high_break | fixed_2pct | tp5 | Y | 1.4380 | 31534 | 39 | 0.4615 | 5 | 51.9000 | 43.5900 | 53.8500 | 0.0000 | 2.5600 |
| gap2pct_high_break_prev_close_tp3_volY | 2% | high_break | prev_close | tp3 | Y | 1.4368 | 17994 | 21 | 0.6667 | 2 | 96.3000 | 66.6700 | 19.0500 | 0.0000 | 14.2900 |
| gap2pct_high_break_prev_close_tp3_volN | 2% | high_break | prev_close | tp3 | N | 1.4368 | 17994 | 21 | 0.6667 | 2 | 96.3000 | 66.6700 | 19.0500 | 0.0000 | 14.2900 |
| gap1pct_close_prev_close_tp5_volY | 1% | close | prev_close | tp5 | Y | 1.4228 | 51204 | 51 | 0.5098 | 5 | 149.3000 | 47.0600 | 27.4500 | 0.0000 | 25.4900 |
| gap1pct_high_break_prev_close_tp5_volN | 1% | high_break | prev_close | tp5 | N | 1.3918 | 35808 | 40 | 0.5250 | 5 | 154.8000 | 47.5000 | 25.0000 | 0.0000 | 27.5000 |
| gap1pct_close_prev_close_tp5_volN | 1% | close | prev_close | tp5 | N | 1.3775 | 47225 | 52 | 0.5000 | 5 | 149.1000 | 46.1500 | 28.8500 | 0.0000 | 25.0000 |
| gap1pct_high_break_fixed_2pct_tp5_volN | 1% | high_break | fixed_2pct | tp5 | N | 1.3654 | 27706 | 40 | 0.4500 | 6 | 52.4000 | 42.5000 | 55.0000 | 0.0000 | 2.5000 |
| gap1pct_high_break_fixed_2pct_trail_only_volY | 1% | high_break | fixed_2pct | trail_only | Y | 1.3358 | 19879 | 39 | 0.4103 | 6 | 37.8000 | 0.0000 | 10.2600 | 87.1800 | 2.5600 |
| gap1pct_high_break_prev_close_trail_only_volY | 1% | high_break | prev_close | trail_only | Y | 1.3235 | 19330 | 39 | 0.4103 | 6 | 41.2000 | 0.0000 | 0.0000 | 97.4400 | 2.5600 |
| gap5pct_close_prev_close_tp3_volY | 5% | close | prev_close | tp3 | Y | 1.3054 | 2811 | 4 | 0.7500 | 1 | 118.0000 | 75.0000 | 0.0000 | 0.0000 | 25.0000 |
| gap5pct_close_prev_close_tp3_volN | 5% | close | prev_close | tp3 | N | 1.3054 | 2811 | 4 | 0.7500 | 1 | 118.0000 | 75.0000 | 0.0000 | 0.0000 | 25.0000 |
| gap2pct_high_break_fixed_2pct_tp3_volY | 2% | high_break | fixed_2pct | tp3 | Y | 1.2979 | 9813 | 21 | 0.4762 | 4 | 14.2000 | 47.6200 | 52.3800 | 0.0000 | 0.0000 |
| gap2pct_high_break_fixed_2pct_tp3_volN | 2% | high_break | fixed_2pct | tp3 | N | 1.2979 | 9813 | 21 | 0.4762 | 4 | 14.2000 | 47.6200 | 52.3800 | 0.0000 | 0.0000 |
| gap1pct_high_break_fixed_2pct_trail_only_volN | 1% | high_break | fixed_2pct | trail_only | N | 1.2903 | 17791 | 40 | 0.4000 | 6 | 37.1000 | 0.0000 | 10.0000 | 87.5000 | 2.5000 |
| gap1pct_high_break_prev_close_trail_only_volN | 1% | high_break | prev_close | trail_only | N | 1.2788 | 17242 | 40 | 0.4000 | 6 | 40.5000 | 0.0000 | 0.0000 | 97.5000 | 2.5000 |
| gap1pct_high_break_first_bar_low_trail_only_volY | 1% | high_break | first_bar_low | trail_only | Y | 1.2315 | 13644 | 39 | 0.3846 | 6 | 34.1000 | 0.0000 | 5.1300 | 92.3100 | 2.5600 |
| gap1pct_high_break_first_bar_low_trail_only_volN | 1% | high_break | first_bar_low | trail_only | N | 1.1894 | 11556 | 40 | 0.3750 | 6 | 33.5000 | 0.0000 | 5.0000 | 92.5000 | 2.5000 |
| gap1pct_close_fixed_2pct_tp3_volY | 1% | close | fixed_2pct | tp3 | Y | 1.1255 | 11275 | 51 | 0.4314 | 8 | 45.6000 | 43.1400 | 52.9400 | 0.0000 | 3.9200 |
| gap1pct_high_break_first_bar_low_tp3_volY | 1% | high_break | first_bar_low | tp3 | Y | 1.1025 | 7544 | 39 | 0.5897 | 2 | 73.6000 | 58.9700 | 33.3300 | 0.0000 | 7.6900 |
| gap5pct_high_break_first_bar_low_tp3_volY | 5% | high_break | first_bar_low | tp3 | Y | 1.0849 | 815 | 3 | 0.6667 | 1 | 20.0000 | 66.6700 | 33.3300 | 0.0000 | 0.0000 |
| gap5pct_high_break_first_bar_low_tp3_volN | 5% | high_break | first_bar_low | tp3 | N | 1.0849 | 815 | 3 | 0.6667 | 1 | 20.0000 | 66.6700 | 33.3300 | 0.0000 | 0.0000 |
| gap1pct_close_fixed_2pct_tp3_volN | 1% | close | fixed_2pct | tp3 | N | 1.0799 | 7483 | 52 | 0.4231 | 8 | 47.3000 | 42.3100 | 53.8500 | 0.0000 | 3.8500 |
| gap1pct_high_break_first_bar_low_tp3_volN | 1% | high_break | first_bar_low | tp3 | N | 1.0459 | 3560 | 40 | 0.5750 | 3 | 73.6000 | 57.5000 | 35.0000 | 0.0000 | 7.5000 |
| gap1pct_high_break_prev_close_tp3_volY | 1% | high_break | prev_close | tp3 | Y | 1.0430 | 3453 | 39 | 0.6154 | 2 | 111.7000 | 61.5400 | 23.0800 | 0.0000 | 15.3800 |
| gap1pct_high_break_fixed_2pct_tp3_volY | 1% | high_break | fixed_2pct | tp3 | Y | 0.9995 | -33 | 39 | 0.4872 | 5 | 32.0000 | 48.7200 | 51.2800 | 0.0000 | 0.0000 |
| gap1pct_close_first_bar_low_tp3_volY | 1% | close | first_bar_low | tp3 | Y | 0.9884 | -1278 | 51 | 0.5490 | 3 | 60.7000 | 54.9000 | 41.1800 | 0.0000 | 3.9200 |
| gap1pct_close_prev_close_tp3_volY | 1% | close | prev_close | tp3 | Y | 0.9792 | -2389 | 51 | 0.5882 | 3 | 107.8000 | 58.8200 | 25.4900 | 0.0000 | 15.6900 |
| gap1pct_high_break_prev_close_tp3_volN | 1% | high_break | prev_close | tp3 | N | 0.9753 | -2126 | 40 | 0.6000 | 3 | 112.3000 | 60.0000 | 25.0000 | 0.0000 | 15.0000 |
| gap3pct_high_break_fixed_2pct_tp3_volY | 3% | high_break | fixed_2pct | tp3 | Y | 0.9731 | -442 | 9 | 0.5556 | 3 | 17.8000 | 55.5600 | 44.4400 | 0.0000 | 0.0000 |
| gap3pct_high_break_fixed_2pct_tp3_volN | 3% | high_break | fixed_2pct | tp3 | N | 0.9731 | -442 | 9 | 0.5556 | 3 | 17.8000 | 55.5600 | 44.4400 | 0.0000 | 0.0000 |
| gap1pct_close_first_bar_low_tp3_volN | 1% | close | first_bar_low | tp3 | N | 0.9674 | -3661 | 52 | 0.5385 | 4 | 61.0000 | 53.8500 | 42.3100 | 0.0000 | 3.8500 |
| gap1pct_close_prev_close_tp3_volN | 1% | close | prev_close | tp3 | N | 0.9465 | -6368 | 52 | 0.5769 | 4 | 108.4000 | 57.6900 | 26.9200 | 0.0000 | 15.3800 |
| gap1pct_high_break_fixed_2pct_tp3_volN | 1% | high_break | fixed_2pct | tp3 | N | 0.9455 | -3861 | 40 | 0.4750 | 6 | 33.0000 | 47.5000 | 52.5000 | 0.0000 | 0.0000 |
| gap5pct_high_break_prev_close_tp3_volY | 5% | high_break | prev_close | tp3 | Y | 0.8544 | -1777 | 3 | 0.6667 | 1 | 131.7000 | 66.6700 | 0.0000 | 0.0000 | 33.3300 |
| gap5pct_high_break_prev_close_tp3_volN | 5% | high_break | prev_close | tp3 | N | 0.8544 | -1777 | 3 | 0.6667 | 1 | 131.7000 | 66.6700 | 0.0000 | 0.0000 | 33.3300 |
| gap5pct_high_break_fixed_2pct_tp3_volY | 5% | high_break | fixed_2pct | tp3 | Y | 0.8397 | -1137 | 3 | 0.3333 | 2 | 6.0000 | 33.3300 | 66.6700 | 0.0000 | 0.0000 |
| gap5pct_high_break_fixed_2pct_tp3_volN | 5% | high_break | fixed_2pct | tp3 | N | 0.8397 | -1137 | 3 | 0.3333 | 2 | 6.0000 | 33.3300 | 66.6700 | 0.0000 | 0.0000 |
| gap3pct_high_break_fixed_2pct_tp5_volY | 3% | high_break | fixed_2pct | tp5 | Y | 0.8140 | -3976 | 9 | 0.4444 | 5 | 82.3000 | 44.4400 | 55.5600 | 0.0000 | 0.0000 |
| gap3pct_high_break_fixed_2pct_tp5_volN | 3% | high_break | fixed_2pct | tp5 | N | 0.8140 | -3976 | 9 | 0.4444 | 5 | 82.3000 | 44.4400 | 55.5600 | 0.0000 | 0.0000 |
| gap5pct_close_first_bar_low_tp5_volY | 5% | close | first_bar_low | tp5 | Y | 0.8038 | -1848 | 4 | 0.2500 | 3 | 194.8000 | 25.0000 | 25.0000 | 0.0000 | 50.0000 |
| gap5pct_close_first_bar_low_tp5_volN | 5% | close | first_bar_low | tp5 | N | 0.8038 | -1848 | 4 | 0.2500 | 3 | 194.8000 | 25.0000 | 25.0000 | 0.0000 | 50.0000 |
| gap3pct_high_break_first_bar_low_trail_only_volY | 3% | high_break | first_bar_low | trail_only | Y | 0.6666 | -3066 | 9 | 0.5556 | 3 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_high_break_first_bar_low_trail_only_volN | 3% | high_break | first_bar_low | trail_only | N | 0.6666 | -3066 | 9 | 0.5556 | 3 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_high_break_prev_close_trail_only_volY | 3% | high_break | prev_close | trail_only | Y | 0.6666 | -3066 | 9 | 0.5556 | 3 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_high_break_prev_close_trail_only_volN | 3% | high_break | prev_close | trail_only | N | 0.6666 | -3066 | 9 | 0.5556 | 3 | 12.2000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap3pct_high_break_fixed_2pct_trail_only_volY | 3% | high_break | fixed_2pct | trail_only | Y | 0.6542 | -3241 | 9 | 0.5556 | 3 | 12.1000 | 0.0000 | 11.1100 | 88.8900 | 0.0000 |
| gap3pct_high_break_fixed_2pct_trail_only_volN | 3% | high_break | fixed_2pct | trail_only | N | 0.6542 | -3241 | 9 | 0.5556 | 3 | 12.1000 | 0.0000 | 11.1100 | 88.8900 | 0.0000 |
| gap5pct_close_prev_close_tp5_volY | 5% | close | prev_close | tp5 | Y | 0.6303 | -4441 | 4 | 0.2500 | 3 | 278.5000 | 25.0000 | 0.0000 | 0.0000 | 75.0000 |
| gap5pct_close_prev_close_tp5_volN | 5% | close | prev_close | tp5 | N | 0.6303 | -4441 | 4 | 0.2500 | 3 | 278.5000 | 25.0000 | 0.0000 | 0.0000 | 75.0000 |
| gap5pct_high_break_first_bar_low_tp5_volY | 5% | high_break | first_bar_low | tp5 | Y | 0.5250 | -6999 | 3 | 0.3333 | 2 | 138.0000 | 33.3300 | 33.3300 | 0.0000 | 33.3300 |
| gap5pct_high_break_first_bar_low_tp5_volN | 5% | high_break | first_bar_low | tp5 | N | 0.5250 | -6999 | 3 | 0.3333 | 2 | 138.0000 | 33.3300 | 33.3300 | 0.0000 | 33.3300 |
| gap5pct_high_break_prev_close_tp5_volY | 5% | high_break | prev_close | tp5 | Y | 0.4465 | -9592 | 3 | 0.3333 | 2 | 249.7000 | 33.3300 | 0.0000 | 0.0000 | 66.6700 |
| gap5pct_high_break_prev_close_tp5_volN | 5% | high_break | prev_close | tp5 | N | 0.4465 | -9592 | 3 | 0.3333 | 2 | 249.7000 | 33.3300 | 0.0000 | 0.0000 | 66.6700 |
| gap5pct_high_break_first_bar_low_trail_only_volY | 5% | high_break | first_bar_low | trail_only | Y | 0.0641 | -5334 | 3 | 0.3333 | 2 | 7.0000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_first_bar_low_trail_only_volN | 5% | high_break | first_bar_low | trail_only | N | 0.0641 | -5334 | 3 | 0.3333 | 2 | 7.0000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_prev_close_trail_only_volY | 5% | high_break | prev_close | trail_only | Y | 0.0641 | -5334 | 3 | 0.3333 | 2 | 7.0000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_prev_close_trail_only_volN | 5% | high_break | prev_close | trail_only | N | 0.0641 | -5334 | 3 | 0.3333 | 2 | 7.0000 | 0.0000 | 0.0000 | 100.0000 | 0.0000 |
| gap5pct_high_break_fixed_2pct_trail_only_volY | 5% | high_break | fixed_2pct | trail_only | Y | 0.0622 | -5510 | 3 | 0.3333 | 2 | 6.7000 | 0.0000 | 33.3300 | 66.6700 | 0.0000 |
| gap5pct_high_break_fixed_2pct_trail_only_volN | 5% | high_break | fixed_2pct | trail_only | N | 0.0622 | -5510 | 3 | 0.3333 | 2 | 6.7000 | 0.0000 | 33.3300 | 66.6700 | 0.0000 |
| gap5pct_close_fixed_2pct_tp5_volY | 5% | close | fixed_2pct | tp5 | Y | 0.0000 | -10526 | 4 | 0.0000 | 4 | 98.5000 | 0.0000 | 75.0000 | 0.0000 | 25.0000 |
| gap5pct_close_fixed_2pct_tp5_volN | 5% | close | fixed_2pct | tp5 | N | 0.0000 | -10526 | 4 | 0.0000 | 4 | 98.5000 | 0.0000 | 75.0000 | 0.0000 | 25.0000 |
| gap5pct_high_break_fixed_2pct_tp5_volY | 5% | high_break | fixed_2pct | tp5 | Y | 0.0000 | -12013 | 3 | 0.0000 | 3 | 113.0000 | 0.0000 | 100.0000 | 0.0000 | 0.0000 |
| gap5pct_high_break_fixed_2pct_tp5_volN | 5% | high_break | fixed_2pct | tp5 | N | 0.0000 | -12013 | 3 | 0.0000 | 3 | 113.0000 | 0.0000 | 100.0000 | 0.0000 | 0.0000 |

## 선정 기준 통과 조합 (OLD 기준, NEW PF>1.0 교차 검증)

선정 기준 미달 — 전 조합 비활성 (`gg_enabled: false` 유지). gap_and_go.enabled: false 유지.