mkdir -p logs
mkdir -p runs
time $OPENROAD_EXE  -exit -python run_ord.py  --group-mode split --k 1 --output runs/7c | tee logs/7c &
time $OPENROAD_EXE  -exit -python run_ord.py   --group-mode even --k 1 --output runs/7d | tee logs/7d &
time $OPENROAD_EXE  -exit -python run_ord.py   --group-mode split --k 2 --output runs/7e | tee logs/7e &

wait