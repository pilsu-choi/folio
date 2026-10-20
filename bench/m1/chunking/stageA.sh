# Stage A: min=128 x all max, plus fixed baselines
A=$(for M in 256 512 768 1024 1536 2048; do printf "adaptive_max${M}_min128,fixed_max${M},"; done)fixed_max512_ov64,fixed_max1024_ov128
echo $A
