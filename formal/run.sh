#!/usr/bin/env bash
# Run the SymbiYosys jobs in formal/ against rtl/.
#
# usage: formal/run.sh [JOB[:TASK,TASK...] ...]
#        JOB = fifo | master | axi; no argument runs every task of every job.
#        Example: formal/run.sh fifo master:prf_w4,cvr
#
# Yosys has no `bind`, so each job runs on a formal-only copy of the RTL
# module with its property file included just before `endmodule`; the copy
# is otherwise byte-identical to rtl/. Work directories and logs go to
# formal/work/. Exit status is non-zero unless every task ends in PASS
# (proofs proven, covers all reached, bounded checks without a failure).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RTL=${RTL:-$HERE/../rtl}
WORK=${FORMAL_WORK:-$HERE/work}
SBY=${SBY:-sby}
TIMEOUT=${FORMAL_TIMEOUT:-1800}

# formal copy: <module>.sv with `include "<props>" before its endmodule
fv_copy() {
  local mod=$1 props=$2 dir=$3
  sed "/^endmodule/i \`include \"$props\"" "$RTL/$mod.sv" > "$dir/${mod}_fv.sv"
  if [ "$(grep -c "^\`include \"$props\"" "$dir/${mod}_fv.sv")" != 1 ]; then
    echo "formal/run.sh: could not insert $props into $mod.sv" >&2
    return 1
  fi
}

prepare() {
  local job=$1 dir=$WORK/$1
  rm -rf "$dir"
  mkdir -p "$dir"
  cp "$HERE/$job"/* "$dir"/
  case $job in
    fifo)   fv_copy spi_fifo fifo_props.svh "$dir" ;;
    master) cp "$RTL/spi_fifo.sv" "$dir"/ && fv_copy spi_master master_props.svh "$dir" ;;
    axi)    cp "$RTL/axi4lite_regfile.sv" "$dir"/ && fv_copy axi4lite_slave axi_props.svh "$dir" ;;
    *)      echo "formal/run.sh: unknown job $job" >&2; return 1 ;;
  esac
}

tasks_of() {
  sed -n '/^\[tasks\]/,/^\[/p' "$HERE/$1/$1.sby" | grep -vE '^\[|^#|^[[:space:]]*$' | awk '{print $1}'
}

specs=("$@")
[ ${#specs[@]} -eq 0 ] && specs=(fifo master axi)
fails=0
total=0
for spec in "${specs[@]}"; do
  job=${spec%%:*}
  prepare "$job" || { fails=$((fails + 1)); continue; }
  if [[ $spec == *:* ]]; then tasks=${spec#*:}; tasks=${tasks//,/ }; else tasks=$(tasks_of "$job"); fi
  for t in $tasks; do
    total=$((total + 1))
    log=$WORK/$job/sby_$t.log
    t0=$(date +%s)
    (cd "$WORK/$job" && timeout "$TIMEOUT" "$SBY" -f "$job.sby" "$t" > "$log" 2>&1)
    status=$(grep -E 'DONE \(' "$log" | tail -1 | sed -E 's/.*DONE \(([A-Z]+).*/\1/')
    status=${status:-ERROR}
    detail=""
    [ "$status" = PASS ] || detail=$(grep -oE '(failed assertion|unreached cover statement) [^ ]+' "$log" \
                                     | sort -u | head -3 | tr '\n' ' ')
    printf '%-7s %-6s %-14s %4ds  %s\n' "$status" "$job" "$t" $(( $(date +%s) - t0 )) "$detail"
    [ "$status" = PASS ] || fails=$((fails + 1))
  done
done
echo "formal: $((total - fails))/$total tasks passed"
[ $fails -eq 0 ]
