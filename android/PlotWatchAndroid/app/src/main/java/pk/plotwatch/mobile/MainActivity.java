package pk.plotwatch.mobile;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int NAVY = Color.rgb(16,47,80), BLUE = Color.rgb(20,106,185);
    private static final int MUTED = Color.rgb(95,111,131), BG = Color.rgb(244,247,251);
    private static final int TEAL = Color.rgb(12,132,110);
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private LinearLayout content;
    private EditText plotInput, cityInput, societyInput, blockInput, serverInput, tokenInput;
    private int activeJob = -1, activeWatch = -1;
    private Runnable polling;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences("plotwatch_prefs", MODE_PRIVATE);
        showSearch();
    }

    @Override public void onDestroy() {
        stopPolling();
        io.shutdownNow();
        super.onDestroy();
    }

    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private GradientDrawable background(int color, int radius) {
        GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius)); return d;
    }
    private LinearLayout column() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private TextView text(String s, int size, int color, boolean bold) {
        TextView t = new TextView(this); t.setText(s); t.setTextSize(size); t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return t;
    }
    private void margin(LinearLayout parent, View child, int bottom) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2); p.bottomMargin = dp(bottom); parent.addView(child,p);
    }
    private LinearLayout card() {
        LinearLayout l = column(); l.setPadding(dp(17),dp(17),dp(17),dp(17));
        l.setBackground(background(Color.WHITE,16));
        margin(content,l,14);
        return l;
    }
    private void section(String s) { margin(content,text(s,18,NAVY,true),12); }
    private void line(LinearLayout parent,String s,int size, int color) { margin(parent,text(s,size,color,false),9); }
    private void setup(String subtitle) {
        LinearLayout root = column(); root.setBackgroundColor(BG);
        setContentView(root);
        LinearLayout head=column(); head.setPadding(dp(20),dp(24),dp(20),dp(18)); head.setBackgroundColor(NAVY);
        margin(head,text("Pakistan PlotWatch",25,Color.WHITE,true),4);
        margin(head,text(subtitle,13,Color.rgb(212,228,243),false),3);
        root.addView(head);
        LinearLayout nav=new LinearLayout(this); nav.setPadding(dp(12),dp(10),dp(12),dp(9));
        nav.setBackgroundColor(Color.WHITE);
        addNav(nav,"Search",this::showSearch); addNav(nav,"Tracked",this::showTracked); addNav(nav,"Settings",this::showSettings);
        root.addView(nav);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true);
        content=column(); content.setPadding(dp(16),dp(20),dp(16),dp(30));
        scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1f));
    }
    private void addNav(LinearLayout nav,String label,Runnable action) {
        Button b=new Button(this); b.setText(label); b.setTextColor(BLUE); b.setAllCaps(false);
        b.setTextSize(13); b.setOnClickListener(v->{stopPolling(); action.run();});
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(48),1f);
        nav.addView(b,p);
    }
    private EditText field(LinearLayout parent,String label,String hint,String start) {
        margin(parent,text(label,13,NAVY,true),6);
        EditText e=new EditText(this); e.setSingleLine(true); e.setTextSize(15); e.setHint(hint);
        e.setPadding(dp(13),dp(10),dp(13),dp(10)); e.setBackground(background(BG,10));
        e.setText(start); margin(parent,e,16); return e;
    }
    private Button button(LinearLayout parent,String title,Runnable action,boolean primary) {
        Button b=new Button(this); b.setText(title); b.setTextSize(14); b.setAllCaps(false);
        b.setTextColor(primary?Color.WHITE:BLUE); b.setBackground(background(primary?BLUE:BG,12));
        b.setOnClickListener(v->action.run()); margin(parent,b,8); return b;
    }
    private ApiClient client() {
        String server=prefs.getString("server", "").trim();
        String token=prefs.getString("token", "").trim();
        if (server.isEmpty() || token.isEmpty()) throw new IllegalStateException("Open Settings and save your HTTPS server URL and access token.");
        return new ApiClient(server, token);
    }
    interface Work { JSONObject call(ApiClient client) throws Exception; }
    interface Success { void done(JSONObject obj); }
    private void request(Work work,Success done) {
        io.submit(()->{
            try { JSONObject result=work.call(client()); ui.post(()->{if (!isFinishing()) done.done(result);}); }
            catch(Exception ex) { ui.post(()->{if (!isFinishing()) error(ex.getMessage());}); }
        });
    }
    private void error(String message) { Toast.makeText(this, message==null?"Request failed":message, Toast.LENGTH_LONG).show(); }
    private String value(EditText field) { return field.getText().toString().trim(); }

    private void showSearch() {
        setup("Find publicly advertised plots across Pakistan");
        LinearLayout box=card();
        margin(box,text("Find a plot",20,NAVY,true),8);
        line(box,"Search accessible property websites automatically. No listing URL required.",13,MUTED);
        plotInput=field(box,"Plot number","e.g. 301",prefs.getString("plot",""));
        cityInput=field(box,"City","Rawalpindi or Islamabad",prefs.getString("city","Rawalpindi"));
        societyInput=field(box,"Housing society","e.g. Bahria Town Phase 8",prefs.getString("society",""));
        blockInput=field(box,"Block / sector (optional)","e.g. Block J",prefs.getString("block",""));
        button(box,"Search and track this plot",this::submitSearch,true);
        LinearLayout note=card(); margin(note,text("How results are checked",17,NAVY,true),8);
        line(note,"Verified matches require accessible webpage HTML. Search-discovered URLs are shown separately as unverified. No match does not mean the plot is not for sale.",13,MUTED);
        line(note,"Successfully completed scans repeat after 15 days. Partial scans retry sooner while the backend stays online.",13,MUTED);
    }
    private void submitSearch() {
        String p=value(plotInput),c=value(cityInput),s=value(societyInput),b=value(blockInput);
        if(p.isEmpty() || c.isEmpty() || s.isEmpty()) {error("Plot number, city and society are required");return;}
        prefs.edit().putString("plot",p).putString("city",c).putString("society",s).putString("block",b).apply();
        try {
            JSONObject body=new JSONObject(); body.put("plot_number",p); body.put("city",c); body.put("society",s); body.put("block",b);
            request(api->api.request("POST","/api/watches",body),res->{
                activeJob=res.optInt("job_id",-1); activeWatch=res.optInt("watch_id",-1);
                showProgress("Search queued on cloud server"); pollJob(activeJob, activeWatch);
            });
        }catch(Exception ex){error(ex.getMessage());}
    }
    private void showProgress(String status) {
        setup("Your crawler is checking candidate websites");
        LinearLayout box=card(); margin(box,text("Searching...",22,NAVY,true),9);
        line(box,status,15,MUTED);
        line(box,"This can take a few minutes, especially when websites restrict automated access.",13,MUTED);
        button(box,"View tracked plots",this::showTracked,false);
    }
    private void stopPolling() { if(polling!=null)ui.removeCallbacks(polling); polling=null; }
    private void pollJob(int job,int watch) {
        if(job<0)return;
        stopPolling();
        polling=()->request(api->api.request("GET","/api/jobs/"+job,null),res->{
            String state=res.optString("status", "queued");
            if(state.equals("completed") || state.equals("failed")) {
                stopPolling(); showDetails(watch,res.optString("report",""));
            }else{
                if(content!=null && content.getChildCount()>0){
                    // Keep UI stable while waiting.
                }
                if(polling!=null)ui.postDelayed(polling,4000);
            }
        });
        ui.postDelayed(polling,1500);
    }
    private void showTracked() {
        setup("Saved plots and 15-day monitoring");
        section("Tracked plots");
        request(api->api.request("GET","/api/watches",null),res->{
            JSONArray arr=res.optJSONArray("items");
            if(arr==null || arr.length()==0){line(card(),"No plots tracked yet. Search for a plot first.",15,MUTED);return;}
            for(int i=0;i<arr.length();i++) {
                JSONObject w=arr.optJSONObject(i); if(w==null)continue;
                final int id=w.optInt("id");
                LinearLayout box=card();
                margin(box,text("Plot "+w.optString("plot_number")+" · "+w.optString("society"),17,NAVY,true),7);
                line(box,w.optString("city")+"  "+w.optString("block"),13,MUTED);
                line(box,w.optString("last_status","Not checked"),13,TEAL);
                line(box,"Next check: "+shortDate(w.optString("next_check_at","Pending")),12,MUTED);
                button(box,"View matches and evidence",()->showDetails(id,""),true);
                button(box,"Refresh now",()->refresh(id),false);
            }
        });
        button(content,"Recent scan history",this::showHistory,false);
    }
    private String shortDate(String iso) { return iso.length()>16?iso.substring(0,16).replace('T',' ')+" UTC":iso; }
    private void refresh(int watchId) {
        request(api->api.request("POST","/api/watches/"+watchId+"/refresh",new JSONObject()),res->{
            int id=res.optInt("job_id"); showProgress("Manual refresh queued"); pollJob(id,watchId);
        });
    }
    private void showDetails(int id,String report) {
        setup("Listing evidence and source links");
        request(api->api.request("GET","/api/watches/"+id,null),res->{
            JSONObject w=res.optJSONObject("watch");
            if(w==null){error("Missing plot details");return;}
            LinearLayout box=card();
            margin(box,text("Plot "+w.optString("plot_number"),24,NAVY,true),5);
            line(box,w.optString("society")+", "+w.optString("block")+", "+w.optString("city"),14,MUTED);
            line(box,"Status: "+w.optString("last_status","Not checked"),13,TEAL);
            line(box,"Last check: "+shortDate(w.optString("last_checked_at","Never")),12,MUTED);
            if(!report.isEmpty())line(box,report,12,MUTED);
            button(box,"Refresh this plot",()->refresh(id),false);
            JSONArray matches=res.optJSONArray("findings");
            section("HTML-verified matching advertisements ("+(matches==null?0:matches.length())+")");
            if(matches==null || matches.length()==0)line(card(),"No verified matching page found in the latest accessible scan.",14,MUTED);
            if(matches!=null)for(int i=0;i<matches.length();i++){
                JSONObject item=matches.optJSONObject(i); if(item==null)continue;
                LinearLayout c=card(); margin(c,text(item.optString("title","Property listing"),15,NAVY,true),8);
                line(c,item.optString("confidence","Verified page"),12,TEAL);
                line(c,item.optString("source",""),12,MUTED);
                line(c,item.optString("snippet",""),13,MUTED);
                String url=item.optString("url",""); button(c,"Open source page",()->openUrl(url),false);
            }
            JSONArray urls=res.optJSONArray("discovered_links");
            section("Discovered but unverified URLs ("+(urls==null?0:urls.length())+")");
            if(urls==null || urls.length()==0)line(card(),"No unverified discovery links recorded.",13,MUTED);
            if(urls!=null)for(int i=0;i<Math.min(urls.length(),20);i++){
                JSONObject item=urls.optJSONObject(i); if(item==null)continue;
                String url=item.optString("url","");
                LinearLayout c=card(); line(c,url,12,MUTED); button(c,"Open candidate",()->openUrl(url),false);
            }
        });
    }
    private void openUrl(String url) {
        if(!url.startsWith("https://") && !url.startsWith("http://")){error("Invalid website link");return;}
        startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)));
    }
    private void showHistory() {
        setup("Recent crawler activity"); section("Search history");
        request(api->api.request("GET","/api/history",null),res->{
            JSONArray arr=res.optJSONArray("items");
            if(arr==null || arr.length()==0)line(card(),"No scans recorded.",14,MUTED);
            if(arr!=null)for(int i=0;i<arr.length();i++){
                JSONObject r=arr.optJSONObject(i); if(r==null)continue;
                LinearLayout b=card(); margin(b,text("Plot "+r.optString("plot_number")+" · "+r.optString("society"),16,NAVY,true),6);
                line(b,shortDate(r.optString("checked_at",""))+" — "+r.optString("status"),13,MUTED);
                line(b,"Matches: "+r.optInt("result_count")+" | Fetched: "+r.optInt("pages_fetched")+" | Blocked: "+r.optInt("pages_blocked"),12,MUTED);
            }
        });
    }
    private void showSettings() {
        setup("Connect the Android app to your cloud crawler");
        LinearLayout box=card(); margin(box,text("Cloud connection",20,NAVY,true),10);
        line(box,"Deploy the included Python backend, then paste its HTTPS address and your private app access token.",13,MUTED);
        serverInput=field(box,"Backend HTTPS URL","https://your-app.up.railway.app",prefs.getString("server",""));
        tokenInput=field(box,"PlotWatch access token","Server PLOTWATCH_APP_TOKEN",prefs.getString("token",""));
        tokenInput.setInputType(129);
        button(box,"Save connection",()->{
            String s=value(serverInput).replaceAll("/+$", "");
            if(!s.startsWith("https://")||!s.contains(".")){error("Enter a valid public HTTPS URL");return;}
            if(value(tokenInput).length()<16){error("Access token is too short");return;}
            prefs.edit().putString("server",s).putString("token",value(tokenInput)).apply();
            Toast.makeText(this,"Connection saved on this device",Toast.LENGTH_SHORT).show();
        },true);
        button(box,"Test server and login",()->{
            String s=value(serverInput).replaceAll("/+$", "");
            String t=value(tokenInput);
            if(!s.startsWith("https://")){error("HTTPS required");return;}
            io.submit(()->{
                try{
                    JSONObject result=new ApiClient(s,t).request("GET","/api/watches",null);
                    ui.post(()->Toast.makeText(this,"Connected successfully",Toast.LENGTH_LONG).show());
                }catch(Exception ex){ui.post(()->error(ex.getMessage()));}
            });
        },false);
        LinearLayout note=card(); margin(note,text("Privacy",16,NAVY,true),7);
        line(note,"The app stores the pilot access token in private app preferences. Do not distribute it publicly. The Serper API key stays on the server, never inside the APK.",13,MUTED);
    }
}
