F=$(curl -s --max-time 20 https://pypi.tuna.tsinghua.edu.cn/simple/vllm/ | grep -o 'vllm-0.30.0-[^"#]*x86_64[^"#]*\.whl' | head -1)
echo "file=$F"
for base in https://pypi.tuna.tsinghua.edu.cn/simple https://mirror.sjtu.edu.cn/pypi/web/simple https://pypi.mirrors.ustc.edu.cn/simple https://mirrors.aliyun.com/pypi/simple https://pypi.org/simple; do
 U=$(curl -sL --max-time 20 $base/vllm/ | grep -o "href=\"[^\"]*$F[^\"]*\"" | head -1 | sed 's/href="//;s/"$//;s/#.*//')
 case "$U" in http*) ;; ../*|/*) U=$(python3 -c "import urllib.parse,sys;print(urllib.parse.urljoin(sys.argv[1]+'/vllm/',sys.argv[2]))" $base "$U");; esac
 curl -sL -r 0-67108863 -o /dev/null --max-time 30 -w "%{http_code} %{size_download} %{speed_download} $base\n" "$U"
done
