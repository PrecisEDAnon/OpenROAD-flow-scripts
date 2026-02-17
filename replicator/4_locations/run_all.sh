mkdir -p logs
mkdir -p runs
time $OPENROAD_EXE  -exit -python run_ord.py  --begin-mode lower_left --end-mode upper_right --k 1 --output runs/4a | tee logs/4a &
time $OPENROAD_EXE  -exit -python run_ord.py  --begin-mode lower_left --end-mode lower_left --k 2 --output runs/4b | tee logs/4b &
time $OPENROAD_EXE  -exit -python run_ord.py  --begin-mode lower_left --end-mode upper_right --k 2 --output runs/4c | tee logs/4c &
time $OPENROAD_EXE  -exit -python run_ord.py  --begin-mode upper_left --end-mode upper_right --k 2 --output runs/4d | tee logs/4d &

wait