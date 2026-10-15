package pk.plotwatch.mobile;

import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class ApiClient {
    private final String base;
    private final String token;

    ApiClient(String base, String token) {
        this.base = base.replaceAll("/+$", "");
        this.token = token.trim();
    }

    JSONObject request(String method, String path, JSONObject requestBody) throws Exception {
        if (!base.startsWith("https://")) throw new IllegalArgumentException("Server address must begin with https://");
        URL endpoint = new URL(base + path);
        if (!endpoint.getProtocol().equals("https")) throw new IllegalArgumentException("HTTPS required");
        HttpURLConnection connection = (HttpURLConnection) endpoint.openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(15000);
        connection.setReadTimeout(30000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Authorization", "Bearer " + token);
        if (requestBody != null) {
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            byte[] body = requestBody.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream out = connection.getOutputStream()) { out.write(body); }
        }
        try {
            int status = connection.getResponseCode();
            InputStream stream = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            if (stream != null) {
                try (InputStream in = stream) {
                    byte[] bytes = new byte[8192];
                    int len;
                    while ((len = in.read(bytes)) != -1) {
                        if (out.size() + len > 1_000_000) throw new Exception("Response is too large");
                        out.write(bytes, 0, len);
                    }
                }
            }
            String result = out.toString(StandardCharsets.UTF_8.name());
            JSONObject json = result.isEmpty() ? new JSONObject() : new JSONObject(result);
            if (status >= 300) throw new Exception("HTTP " + status + ": " + json.optString("detail", result));
            return json;
        } finally {
            connection.disconnect();
        }
    }
}
