# Stage B: min sweep at the best max by Stage A (256) plus 512/768/1024 for sensitivity; Stage A configs are kept
(bash stageA.sh | tr -d '\n'; for M in 256 512 768 1024; do for m in 0 64 256; do [ $m -lt $M ] && printf ",adaptive_max${M}_min${m}"; done; done)
