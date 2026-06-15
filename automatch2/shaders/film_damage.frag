#version 330 core
in vec2 v_uv;
out vec4 fragColor;
uniform sampler2D u_tex;
uniform float u_mix;
uniform float u_time;
uniform vec2  u_resolution;
uniform float u_p0; // grain amount
uniform float u_p1; // damage amount

// inline hash helpers
float h11(float x){ return fract(sin(x*127.1)*43758.5453); }
float h21(vec2 p){ return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
float vnoise(vec2 p){
    vec2 i=floor(p), f=fract(p);
    f=f*f*(3.0-2.0*f);
    float a=h21(i), b=h21(i+vec2(1,0)), c=h21(i+vec2(0,1)), d=h21(i+vec2(1,1));
    return mix(mix(a,b,f.x), mix(c,d,f.x), f.y);
}

void main(){
    vec2 uv=v_uv;
    vec3 src=texture(u_tex,uv).rgb;
    vec3 col=src;

    float t=u_time;
    float grain=u_p0, dmg=u_p1;

    // --- multi-octave animated grain ---
    vec2 gp=uv*u_resolution;
    float g=0.0, amp=0.55, scale=1.0;
    for(int i=0;i<3;i++){
        float jt=floor(t*24.0)+float(i)*37.0;       // film-rate jitter
        g+=(h21(gp*scale+vec2(jt,jt*1.7))-0.5)*amp;
        scale*=2.3; amp*=0.55;
    }
    float lum=dot(col,vec3(0.299,0.587,0.114));
    col+=g*grain*0.32*(0.4+0.9*(1.0-lum));          // grain rides in shadows/mids

    // --- sparse dust specks (bright + dark), reroll each frame ---
    float fr=floor(t*16.0);
    vec2 dcell=uv*vec2(80.0,55.0);
    vec2 dc=floor(dcell);
    float dh=h21(dc+fr*3.13);
    if(dh>0.992-0.012*dmg){
        float sp=h21(dc.yx+fr*7.7);
        float d=length(fract(dcell)-0.5);
        float speck=smoothstep(0.34,0.0,d);
        col=mix(col, vec3(step(0.5,sp)), speck*dmg);
    }

    // --- flickering vertical scratches ---
    float sc=0.0;
    for(int s=0;s<3;s++){
        float seed=floor(t*3.0)+float(s)*53.0;
        float life=h11(seed+float(s));
        if(life>0.55){
            float xpos=h11(seed*1.7+float(s)*9.0);
            float wob=(vnoise(vec2(uv.y*8.0,seed))-0.5)*0.015;
            float dx=abs(uv.x-(xpos+wob));
            float w=0.0012+0.0016*h11(seed*2.3);
            sc+=smoothstep(w,0.0,dx)*(0.4+0.6*h11(seed*5.1));
        }
    }
    col+=sc*dmg*0.9*vec3(1.0,0.97,0.9);             // bright torn emulsion
    col-=sc*dmg*0.25;                               // thin dark core

    // --- exposure flicker (subtle, film-rate) ---
    float fl=1.0+(vnoise(vec2(t*7.0,0.0))-0.5)*0.12*(0.5+dmg);
    fl*= 1.0 - 0.05*dmg*h11(floor(t*12.0));         // occasional dropouts
    col*=fl;

    // --- vignette ---
    vec2 vd=uv-0.5;
    float vig=smoothstep(0.85,0.25,length(vd*vec2(1.1,1.0)));
    col*=mix(1.0,vig,0.55);

    // gentle warm celluloid bias
    col*=vec3(1.04,1.0,0.93);

    col=clamp(col,0.0,1.0);
    fragColor=vec4(mix(src,col,clamp(u_mix,0.0,1.0)),1.0);
}
