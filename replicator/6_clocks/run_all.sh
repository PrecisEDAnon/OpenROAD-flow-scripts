mkdir -p logs
mkdir -p runs
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode split --k 1  --output runs/6c | tee logs/6c &
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode split --k 2  --output runs/6d | tee logs/6d &
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode split --k 3  --output runs/6e | tee logs/6e &
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode split --k 4  --output runs/6f | tee logs/6f &
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode even --k 2  --output runs/6g | tee logs/6g &
time $OPENROAD_EXE  -exit -python run_ord.py --clock-mode even --k 4  --output runs/6h | tee logs/6h &

wait