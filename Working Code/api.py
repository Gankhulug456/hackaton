from openai import OpenAI
from docx import Document
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
import json
import mimetypes

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Templates directory for rendering responses
templates = Jinja2Templates(directory="templates")


class DocumentAnalyzer:
    def __init__(self):
        self.client = OpenAI(
            base_url=os.getenv("API_BASE_URL", "http://localhost:1234/v1"),
            api_key=os.getenv("API_KEY", "lm-studio")
        )

    def read_docx(self, file_path):
        """Read content from a DOCX file"""
        try:
            doc = Document(file_path)
            full_text = '\n'.join([paragraph.text for paragraph in doc.paragraphs])
            return full_text
        except Exception as e:
            raise ValueError(f"Error reading DOCX file: {e}")

    def read_pdf(self, file_path):
        """Read content from a PDF file"""
        try:
            reader = PdfReader(file_path)
            return "\n".join(page.extract_text() for page in reader.pages)
        except Exception as e:
            raise ValueError(f"Error reading PDF file: {e}")

    def read_txt(self, file_path):
        """Read content from a TXT file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise ValueError(f"Error reading TXT file: {e}")

    def read_file(self, file_path):
        """Determine file type and extract text using mimetypes"""
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type == "application/pdf":
            return self.read_pdf(file_path)
        elif mime_type in [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ]:
            return self.read_docx(file_path)
        elif mime_type and mime_type.startswith("text/"):
            return self.read_txt(file_path)
        else:
            raise ValueError("Unsupported file type")

    def extract_clauses(self, document_text):
        """Break document into clauses using AI"""
        prompt = f"""
        Break the following contract into separate clauses:
        {document_text}
        Return only the separated clauses.
        """
        response = self.client.chat.completions.create(
            model=os.getenv("MODEL_ID", "model-identifier"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content.split('\n\n')

    def analyze_clause(self, clause_text):
        """Analyze a single clause for risks"""
        prompt = f"""
        Analyze the following contract clause for risk level and provide:
        1. Risk level (Low, Medium, High)
        2. Brief explanation of the risk
        
        Clause: {clause_text}
        """
        response = self.client.chat.completions.create(
            model=os.getenv("MODEL_ID", "model-identifier"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        analysis = response.choices[0].message.content
        return {
            "clause_text": clause_text,
            "analysis": analysis,
            "risk_level": self._extract_risk_level(analysis)
        }

    def _extract_risk_level(self, analysis_text):
        """Extract risk level from analysis text"""
        lower_text = analysis_text.lower()
        if "high risk" in lower_text or "high" in lower_text:
            return "High"
        elif "medium risk" in lower_text or "medium" in lower_text:
            return "Medium"
        return "Low"

    def calculate_risk_score(self, analyzed_clauses, clause_weights=None):
        """
        Calculate an overall risk score for the document.

        Parameters:
        - analyzed_clauses: List of analyzed clauses with their risk levels.
        - clause_weights: Dictionary of clause weights (optional).

        Returns:
        - A dictionary with the overall score and breakdown.
        """
        if not analyzed_clauses:
            return {"overall_score": 0, "risk_breakdown": {}}

        # Default weights for clause importance
        if clause_weights is None:
            clause_weights = {
                "Indemnification": 1.5,
                "Termination": 1.3,
                "Confidentiality": 1.2,
                "Default": 1.0,
            }

        # Risk level weights
        risk_weights = {"High": 3, "Medium": 2, "Low": 1}

        # Calculate weighted score
        total_weighted_score = 0
        total_weight = 0
        risk_breakdown = {"High": 0, "Medium": 0, "Low": 0}

        for clause in analyzed_clauses:
            risk_level = clause["risk_level"]
            risk_score = risk_weights.get(risk_level, 1)
            clause_text = clause["clause_text"]

            # Determine clause importance weight
            importance_weight = 1.0
            for key, weight in clause_weights.items():
                if key.lower() in clause_text.lower():
                    importance_weight = weight
                    break

            # Accumulate scores
            weighted_score = risk_score * importance_weight
            total_weighted_score += weighted_score
            total_weight += importance_weight
            risk_breakdown[risk_level] += 1

        # Normalize the score to a 0–100 scale
        overall_score = (total_weighted_score / (total_weight * 3)) * 100

        return {
            "overall_score": round(overall_score, 2),
            "risk_breakdown": risk_breakdown,
        }

    def analyze_document(self, file_path):
        """Main method to analyze a document"""
        try:
            # Read document
            document_text = self.read_file(file_path)

            # Extract clauses
            clauses = self.extract_clauses(document_text)

            # Analyze each clause
            analyzed_clauses = [self.analyze_clause(clause) for clause in clauses]

            # Calculate risk score
            risk_data = self.calculate_risk_score(analyzed_clauses)

            # Create analysis result
            return {
                "filename": os.path.basename(file_path),
                "clauses": analyzed_clauses,
                "overall_score": risk_data["overall_score"],
                "risk_breakdown": risk_data["risk_breakdown"],
            }
        except Exception as e:
            return {"error": str(e)}


@app.get("/", response_class=HTMLResponse)
async def get_ui():
    """Render UI for inputting file path"""
    return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Document Analyzer</title></head>
        <body>
            <h1>Enter File Path for Analysis</h1>
            <form action="/analyze" method="post">
                <label for="file_path">File Path:</label>
                <input type="text" id="file_path" name="file_path" required>
                <button type="submit">Analyze</button>
            </form>
        </body>
        </html>
    """)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(file_path: str = Form(...)):
    """Analyze a document from the provided file path"""
    analyzer = DocumentAnalyzer()

    if not os.path.isfile(file_path):
        return HTMLResponse(content=f"<h1>Error</h1><p>File not found: {file_path}</p>", status_code=400)

    analysis_result = analyzer.analyze_document(file_path)
    if "error" in analysis_result:
        return HTMLResponse(content=f"<h1>Error</h1><p>{analysis_result['error']}</p>", status_code=500)

    result_html = f"""
        <h1>Analysis Results for {analysis_result['filename']}</h1>
        <p><b>Overall Risk Score:</b> {analysis_result['overall_score']}%</p>
        <ul>
    """
    for i, clause in enumerate(analysis_result["clauses"], 1):
        result_html += f"<li><b>Clause {i}:</b> {clause['clause_text']} <br> Risk: {clause['risk_level']}</li>"
    result_html += "</ul>"

    return HTMLResponse(content=result_html)
