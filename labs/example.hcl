global {
  worker_processes = 4
  max_connections  = 1024
  timeout_keepalive = "65s"
  timeout = "30s"

  logging {
    level  = "info"
    output = "/var/log/myhttpd/access.log"
    format = "json" # json or text
  }
}

server "main-server" {
  host = "example.com"
  port = 80
  root = "./html"

  tls {
    enabled = true
    cert    = "/etc/certs/example.com.crt"
    key     = "/etc/certs/example.com.key"
    min_version = "TLS1.2"
  }

  headers {
    add = {
      "X-Frame-Options" = "DENY"
    }
    remove = ["Server", "X-Powered-By"]
  }

  route "/" {
    type = "static"
    index = ["index.html", "index.htm"]
    
    # ブラウザキャッシュ設定
    headers {
      set = { "Cache-Control" = "public, max-age=3600" }
    }
  }

  route "/admin" {
    type = "static"
    
    # IP制限などのブロックが見やすい
    security {
      ip_allow = ["192.168.10.0/24"]
      deny_all = true
    }
  }

  route "/config" {
    type = "raw"
    
    # 実際にはファイルが存在しても、攻撃者に存在を悟らせないため404を返す
    respond {
      status = 404
      body   = "Not Found"
    }
  }

  route "/v1" {
    type = "proxy"
    
    backend {
      upstream = "http://localhost:9000"
      timeout  = "30s"
      
      # プロキシ時にヘッダーを書き換える
      headers {
        set = { "X-Real-IP" = "$remote_addr" }
      }
    }
  }
}
