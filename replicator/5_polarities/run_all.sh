mkdir -p logs
mkdir -p runs
time $OPENROAD_EXE  -exit -python run_ord.py --polarity-mode split --k 1  --output runs/5c | tee logs/5c &
time $OPENROAD_EXE  -exit -python run_ord.py --polarity-mode split --k 2  --output runs/5d | tee logs/5d &
time $OPENROAD_EXE  -exit -python run_ord.py --polarity-mode even --k 2  --output runs/5e | tee logs/5e &

wait