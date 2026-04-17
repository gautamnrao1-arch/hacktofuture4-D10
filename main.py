import os
import requests
from genai import Client
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

# 1. SETUP & SECURITY
load_dotenv()
Client = Client(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

# 2. RATE LIMITER CONFIGURATION
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class RequestData(BaseModel):
    github_token: str
    repo_name: str
    apply_fix: bool = False

# ------------------- THE 4 AGENTS -----------------

# AGENT 1: MONITORING (Fetches and filters files)
def monitoring_agent(token, repo):
    headers = {"Authorization": f"token {token}"}
    url = f"https://api.github.com/repos/{repo}/contents/"
    res = requests.get(url, headers=headers)
    
    if res.status_code != 200:
        return None
        
    items = res.json()
    allowed_exts = ('.py', '.js', '.ts', '.java', '.cpp')
    # Limits to 3 files to manage token usage
    return [i for i in items if i['type'] == 'file' and i['name'].endswith(allowed_exts)][:3]

# AGENT 2: ANALYSIS (Identifies bugs/vulnerabilities)
def analysis_agent(code, filename):
    prompt = (
        f"AGENT: ANALYSIS\nFILE: {filename}\n"
        "TASK: Identify security risks (OWASP), syntax errors, and logic bugs.\n"
        "FORMAT: \nIssue: \nReason: \nRisk Level: "
    )
    response = model.generate_content(prompt + "\n\nCODE:\n" + code)
    return response.text

# AGENT 3: SUGGESTION (Creates the fix and snippet)
def suggestion_agent(analysis_report):
    prompt = (
        f"AGENT: SUGGESTION\nREPORT: {analysis_report}\n"
        "TASK: Provide a step-by-step manual fix and a corrected code snippet.\n"
        "FORMAT: \nSolution: \nCorrected Code: "
    )
    response = model.generate_content(prompt)
    return response.text

# AGENT 4: REPAIR (Decision engine)
def repair_agent(suggestion, apply_fix_flag):
    status = "Applied (Simulated)" if apply_fix_flag else "Manual Action Required"
    return f"STATUS: {status}\n{suggestion}"

# ----------------- MAIN API ENDPOINT -----------------

@app.post("/analyze")
@limiter.limit("20/minute") # Protects your API from spam
async def analyze_repo(data: RequestData, request: Request):
    try:
        # Step 1: Monitoring
        files = monitoring_agent(data.github_token, data.repo_name)
        if files is None:
            raise HTTPException(status_code=400, detail="Repo not found or Token invalid")

        final_results = []

        for file in files:
            # Fetch Code
            code = requests.get(file["download_url"]).text[:5000] # Character cap

            # Step 2: Analysis
            report = analysis_agent(code, file["name"])
            
            # Step 3: Suggestion
            fix_details = suggestion_agent(report)  
            
            # Step 4: Repair Logic
            final_output = repair_agent(fix_details, data.apply_fix)

            final_results.append({
                "filename": file["name"],
                "analysis": f"{report}\n\n{final_output}" # Merged for frontend
            })

        return final_results

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))