// 자동 생성 — 손으로 고치지 말 것.
// experiments/make_dashboard_data.py가 experiments/results/*.json에서 생성한다.
// 생성 시각: 2026-09-13T12:11:43
window.ANM_DATA = {
 "generated_at": "2026-09-13T12:11:43",
 "generator": "experiments/make_dashboard_data.py",
 "palette": {
  "categorical": [
   "#3987e5",
   "#d95926",
   "#199e70",
   "#c98500",
   "#d55181",
   "#008300",
   "#9085e9",
   "#e66767"
  ],
  "status": {
   "good": "#0ca30c",
   "warning": "#fab219",
   "serious": "#ec835a",
   "critical": "#d03b3b"
  },
  "deemphasis": "#52514e"
 },
 "closed_loop": {
  "n": 50,
  "avg_ttr": 6.88,
  "test_avg_ttr": 6.75,
  "train_avg_ttr": 7.11,
  "n_test": 32,
  "n_train": 18,
  "success_rate": 100.0,
  "rca_first_pct": 100.0,
  "rca_all_pct": 100.0,
  "wasted_per_ep": 0.7,
  "hist_bins": [
   4,
   5,
   6,
   7,
   8,
   9,
   10,
   11
  ],
  "hist_test": [
   1,
   0,
   15,
   10,
   3,
   2,
   1,
   0
  ],
  "hist_train": [
   0,
   1,
   6,
   6,
   3,
   0,
   1,
   1
  ],
  "provenance": {
   "git_commit": "25feed0-dirty",
   "timestamp": "2026-09-13T09:41:41.316545",
   "seed": 42,
   "contract": {
    "obs_version": 1,
    "action_version": 1,
    "reward_version": 1,
    "sim_version": 3
   },
   "condition": "폐쇄 루프 /auto-step, 사이클당 시뮬레이터 1틱(lockstep), 검증 조회는 순수 조회. 2026-09-09 이전 결과는 사이클당 2틱으로 측정되어 직접 비교 불가."
  }
 },
 "ablation": {
  "n_per_mode": 50,
  "modes": [
   {
    "key": "analytics_only",
    "label": "분석만 (규칙 기반 RCA)",
    "avg_ttr": 7.08,
    "success_rate": 100.0,
    "wasted_per_ep": 0.0,
    "rca_all_pct": 100.0
   },
   {
    "key": "maml_only",
    "label": "MAML만 (학습 정책 단독)",
    "avg_ttr": 13.86,
    "success_rate": 14.000000000000002,
    "wasted_per_ep": 0.34,
    "rca_all_pct": 100.0
   },
   {
    "key": "combined",
    "label": "결합 (분석 + MAML)",
    "avg_ttr": 7.0,
    "success_rate": 100.0,
    "wasted_per_ep": 0.68,
    "rca_all_pct": 100.0
   }
  ],
  "provenance": {
   "git_commit": "25feed0-dirty",
   "timestamp": "2026-09-13T09:40:03.734815",
   "seed": 42,
   "contract": {
    "obs_version": 1,
    "action_version": 1,
    "reward_version": 1,
    "sim_version": 3
   },
   "condition": "폐쇄 루프 /auto-step, 사이클당 시뮬레이터 1틱(lockstep), 검증 조회는 순수 조회, 에피소드별 노이즈 seed 고정(42000+i). 2026-09-09 이전 결과는 사이클당 2틱."
  }
 },
 "offline": {
  "agents": [
   {
    "key": "baseline",
    "label": "PPO (Baseline DRL)",
    "avg_ttr": 200.0,
    "success_rate": 0.0,
    "avg_reward": 58.538607999999996,
    "n": 50,
    "solved": 0,
    "unsolved": 50,
    "avg_ttr_solved": null
   },
   {
    "key": "fewshot",
    "label": "MAML (Few-shot)",
    "avg_ttr": 101.93,
    "success_rate": 50.0,
    "avg_reward": 90.795372,
    "n": 50,
    "solved": 25,
    "unsolved": 25,
    "avg_ttr_solved": 3.86
   }
  ],
  "timeout": 200.0,
  "eval_links": [
   "r3-r4",
   "r1-r4"
  ],
  "train_links": [
   "r1-r2",
   "r1-r3",
   "r2-r3",
   "r2-r4"
  ],
  "provenance": {
   "git_commit": "9f74741-dirty",
   "timestamp": "2026-09-13T09:33:26.109649",
   "seed": 42,
   "contract": {
    "obs_version": 1,
    "action_version": 1,
    "reward_version": 1,
    "sim_version": 3
   },
   "condition": "offline NetworkEnv(local_mode, inject_anomalies=False, max_steps=200) 평가 — Analytics override 없음. /auto-step 폐쇄 루프(OODA) 수치와 직접 비교 불가. 미해결 시 TTR=200. 2026-09-09부터 스텝당 시뮬레이터 1틱 (이전에는 로깅용 재조회로 2틱) — 이전 오프라인 결과와 직접 비교 불가."
  }
 },
 "policy": {
  "agents": [
   {
    "key": "ppo",
    "label": "PPO (Baseline DRL)",
    "collapsed": false,
    "top_action": "r1-r4@20",
    "top_share": 0.4583,
    "entropy_bits": 1.6513,
    "distinct": 5,
    "actions": [
     {
      "action": "r1-r4@20",
      "count": 22,
      "share": 0.4583
     },
     {
      "action": "r1-r3@200",
      "count": 19,
      "share": 0.3958
     },
     {
      "action": "r2-r3@200",
      "count": 4,
      "share": 0.0833
     },
     {
      "action": "r1-r2@100",
      "count": 2,
      "share": 0.0417
     },
     {
      "action": "r2-r4@200",
      "count": 1,
      "share": 0.0208
     }
    ]
   },
   {
    "key": "maml",
    "label": "MAML (Few-shot)",
    "collapsed": true,
    "top_action": "r3-r4@100",
    "top_share": 1.0,
    "entropy_bits": 0.0,
    "distinct": 1,
    "actions": [
     {
      "action": "r3-r4@100",
      "count": 48,
      "share": 1.0
     }
    ]
   }
  ],
  "threshold": 0.8,
  "provenance": {
   "git_commit": "7eb1afb-dirty",
   "timestamp": "2026-09-13T09:27:37.335814",
   "seed": null,
   "contract": {
    "obs_version": 1,
    "action_version": 1,
    "reward_version": 1,
    "sim_version": 3
   },
   "condition": "체크포인트별 정책 행동 분포 프로브 (링크마다 혼잡 주입 후 8스텝, 무작위 관측 200회)"
  }
 },
 "training": {
  "algos": [
   {
    "key": "maml",
    "label": "MAML (Few-shot)",
    "total": 500,
    "unit": "meta-iterations",
    "points": [
     {
      "pct": 0.2,
      "progress": 1,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 4.0,
      "progress": 20,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 8.0,
      "progress": 40,
      "entropy_bits": 0.9183,
      "top_share": 0.6667,
      "collapsed": false
     },
     {
      "pct": 12.0,
      "progress": 60,
      "entropy_bits": 1.644,
      "top_share": 0.4583,
      "collapsed": false
     },
     {
      "pct": 16.0,
      "progress": 80,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 20.0,
      "progress": 100,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 24.0,
      "progress": 120,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 28.0,
      "progress": 140,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 32.0,
      "progress": 160,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 36.0,
      "progress": 180,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 40.0,
      "progress": 200,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 44.0,
      "progress": 220,
      "entropy_bits": 1.5613,
      "top_share": 0.375,
      "collapsed": false
     },
     {
      "pct": 48.0,
      "progress": 240,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 52.0,
      "progress": 260,
      "entropy_bits": 0.9799,
      "top_share": 0.5833,
      "collapsed": false
     },
     {
      "pct": 56.0,
      "progress": 280,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 60.0,
      "progress": 300,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 64.0,
      "progress": 320,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 68.0,
      "progress": 340,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 72.0,
      "progress": 360,
      "entropy_bits": 0.5436,
      "top_share": 0.875,
      "collapsed": true
     },
     {
      "pct": 76.0,
      "progress": 380,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 80.0,
      "progress": 400,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 84.0,
      "progress": 420,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 88.0,
      "progress": 440,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 92.0,
      "progress": 460,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 96.0,
      "progress": 480,
      "entropy_bits": 0.5436,
      "top_share": 0.875,
      "collapsed": true
     },
     {
      "pct": 100.0,
      "progress": 500,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     }
    ],
    "first_collapse_pct": 24.0,
    "first_collapse_progress": 120,
    "ends_collapsed": true,
    "provenance": {
     "git_commit": "7eb1afb-dirty",
     "timestamp": "2026-09-13T09:26:53.682003",
     "seed": 42,
     "contract": {
      "obs_version": 1,
      "action_version": 1,
      "reward_version": 1,
      "sim_version": 3
     },
     "condition": "maml 학습 중 20마다 정책 행동 분포 프로브 (손실이 아니라 행동 — VISUALIZATION_PLAN T1)"
    }
   },
   {
    "key": "ppo",
    "label": "PPO (Baseline DRL)",
    "total": 50000,
    "unit": "timesteps",
    "points": [
     {
      "pct": 4.1,
      "progress": 2048,
      "entropy_bits": 1.4056,
      "top_share": 0.5,
      "collapsed": false
     },
     {
      "pct": 8.19,
      "progress": 4096,
      "entropy_bits": 1.8727,
      "top_share": 0.4167,
      "collapsed": false
     },
     {
      "pct": 12.29,
      "progress": 6144,
      "entropy_bits": 0.9544,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 16.38,
      "progress": 8192,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 20.48,
      "progress": 10240,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 24.58,
      "progress": 12288,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 28.67,
      "progress": 14336,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 32.77,
      "progress": 16384,
      "entropy_bits": 0.0,
      "top_share": 1.0,
      "collapsed": true
     },
     {
      "pct": 36.86,
      "progress": 18432,
      "entropy_bits": 0.2499,
      "top_share": 0.9583,
      "collapsed": true
     },
     {
      "pct": 40.96,
      "progress": 20480,
      "entropy_bits": 1.2988,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 45.06,
      "progress": 22528,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 49.15,
      "progress": 24576,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 53.25,
      "progress": 26624,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 57.34,
      "progress": 28672,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 61.44,
      "progress": 30720,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 65.54,
      "progress": 32768,
      "entropy_bits": 1.5284,
      "top_share": 0.4167,
      "collapsed": false
     },
     {
      "pct": 69.63,
      "progress": 34816,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 73.73,
      "progress": 36864,
      "entropy_bits": 1.3471,
      "top_share": 0.5833,
      "collapsed": false
     },
     {
      "pct": 77.82,
      "progress": 38912,
      "entropy_bits": 1.0434,
      "top_share": 0.7083,
      "collapsed": false
     },
     {
      "pct": 81.92,
      "progress": 40960,
      "entropy_bits": 1.3261,
      "top_share": 0.625,
      "collapsed": false
     },
     {
      "pct": 86.02,
      "progress": 43008,
      "entropy_bits": 0.8113,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 90.11,
      "progress": 45056,
      "entropy_bits": 1.1761,
      "top_share": 0.75,
      "collapsed": false
     },
     {
      "pct": 94.21,
      "progress": 47104,
      "entropy_bits": 1.5988,
      "top_share": 0.5833,
      "collapsed": false
     },
     {
      "pct": 98.3,
      "progress": 49152,
      "entropy_bits": 1.5988,
      "top_share": 0.5833,
      "collapsed": false
     },
     {
      "pct": 102.4,
      "progress": 51200,
      "entropy_bits": 1.2721,
      "top_share": 0.6667,
      "collapsed": false
     }
    ],
    "first_collapse_pct": 16.38,
    "first_collapse_progress": 8192,
    "ends_collapsed": false,
    "provenance": {
     "git_commit": "74300b6-dirty",
     "timestamp": "2026-09-13T09:20:34.115307",
     "seed": 42,
     "contract": {
      "obs_version": 1,
      "action_version": 1,
      "reward_version": 1,
      "sim_version": 3
     },
     "condition": "ppo 학습 중 2048마다 정책 행동 분포 프로브 (손실이 아니라 행동 — VISUALIZATION_PLAN T1)"
    }
   }
  ],
  "threshold": 0.8
 },
 "checkpoints": [
  {
   "checkpoint": "maml_network.pt",
   "algo": "maml",
   "git_commit": "7eb1afb-dirty",
   "timestamp": "2026-09-13T09:26:56.158081",
   "seed": 42,
   "train_links": [
    "r1-r2",
    "r1-r3",
    "r2-r3",
    "r2-r4"
   ],
   "collapsed": true,
   "top_action": "r3-r4@100",
   "top_share": 1.0,
   "entropy_bits": 0.0,
   "distinct": 1
  },
  {
   "checkpoint": "ppo_network.zip",
   "algo": "ppo",
   "git_commit": "74300b6-dirty",
   "timestamp": "2026-09-13T09:20:36.712535",
   "seed": 42,
   "train_links": [
    "r1-r2",
    "r1-r3",
    "r2-r3",
    "r2-r4"
   ],
   "collapsed": false,
   "top_action": "r1-r4@20",
   "top_share": 0.4583,
   "entropy_bits": 1.6513,
   "distinct": 5
  }
 ],
 "persistent": {
  "n": 30,
  "overall_avg_ttr": 7.1,
  "early_avg_ttr": 7.27,
  "late_avg_ttr": 6.93,
  "early_adapt_rate": 5.53,
  "late_adapt_rate": 5.8,
  "success_rate": 100.0,
  "wasted_per_ep": 0.73,
  "provenance": {
   "git_commit": "25feed0-dirty",
   "timestamp": "2026-09-13T09:42:52.338410",
   "seed": 42,
   "contract": {
    "obs_version": 1,
    "action_version": 1,
    "reward_version": 1,
    "sim_version": 3
   },
   "condition": "폐쇄 루프 /auto-step, 사이클당 1틱(lockstep). 2026-09-09 이전 결과는 사이클당 2틱."
  }
 }
};
