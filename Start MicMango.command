#!/bin/zsh

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

script_dir="${0:A:h}"
cd "$script_dir" || exit 1

python_path="$script_dir/.venv/bin/python"
if [[ ! -x "$python_path" ]]; then
  echo "没有找到 MicMango 的 Python 环境："
  echo "$python_path"
  echo ""
  echo "请先按照 README.md 的安装步骤创建环境。"
  echo "按任意键关闭这个窗口。"
  read -k 1
  exit 1
fi

echo "正在启动 MicMango 控制台…"
echo "网页会显示今日字符数和语音输入历史。"
echo "关闭这个窗口会停止语音输入。"
echo ""

"$python_path" "$script_dir/server.py" --open

status_code=$?
echo ""
if [[ $status_code -ne 0 ]]; then
  echo "MicMango 没有正常启动。请查看上面的提示。"
else
  echo "MicMango 已停止。"
fi
echo "按任意键关闭这个窗口。"
read -k 1
exit $status_code
