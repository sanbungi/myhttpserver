#!/bin/bash

echo "=================================================="
echo "  Webサーバー 多数接続向け OS設定確認スクリプト"
echo "=================================================="
echo ""

echo "### 1. ファイルディスクリプタ (FD) の制限 ###"
echo "- 現在のログインユーザーのSoft Limit: $(ulimit -Sn)"
echo "- 現在のログインユーザーのHard Limit: $(ulimit -Hn)"
echo "- システム全体の最大値 (fs.file-max):"
sysctl fs.file-max
echo "- 現在の使用状況 (確保済 / 未使用 / 最大値) [file-nr]:"
cat /proc/sys/fs/file-nr
echo ""

echo "### 2. ポートの枯渇（エフェメラルポート） ###"
echo "- 利用可能なローカルポート範囲:"
sysctl net.ipv4.ip_local_port_range
echo ""

echo "### 3. TIME_WAIT ソケットの滞留対策 ###"
echo "- TIME_WAITの再利用設定 (tcp_tw_reuse):"
sysctl net.ipv4.tcp_tw_reuse 2>/dev/null || echo "  net.ipv4.tcp_tw_reuse = 設定なし/非対応"
echo ""

echo "### 4. コネクショントラッキング (nf_conntrack) ###"
if [ -f /proc/sys/net/netfilter/nf_conntrack_max ]; then
    echo "- トラッキング最大値 (nf_conntrack_max):"
    sysctl net.netfilter.nf_conntrack_max
    echo "- 現在のトラッキング数 (nf_conntrack_count):"
    sysctl net.netfilter.nf_conntrack_count 2>/dev/null
else
    echo "- nf_conntrack モジュールはロードされていません。"
    echo "  (ファイアウォールが無効、またはトラッキングが無効なため安全です)"
fi
echo ""

echo "### 5. ソケットのバックログ（キュー）制限 ###"
echo "- OSのAcceptキュー最大長 (somaxconn):"
sysctl net.core.somaxconn
echo "- TCP SYN未完了キュー最大長 (tcp_max_syn_backlog):"
sysctl net.ipv4.tcp_max_syn_backlog
echo "- NICからのパケットキュー最大長 (netdev_max_backlog):"
sysctl net.core.netdev_max_backlog
echo ""

echo "### 6. ネットワーク割り込み処理 (irqbalance) ###"
if command -v systemctl >/dev/null 2>&1; then
    IRQ_STATUS=$(systemctl is-active irqbalance 2>/dev/null)
    if [ "$IRQ_STATUS" = "active" ]; then
        echo "- irqbalance デーモン: 稼働中 (active)"
    else
        echo "- irqbalance デーモン: 停止中、または未インストール ($IRQ_STATUS)"
    fi
else
    echo "- systemctlコマンドがないため確認スキップ"
fi
echo ""
echo "=================================================="
