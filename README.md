# planner_ai_template
Fall 2025 Coding Workshop that uses Google Gemini AI API to create a planner/schedule to complete a project. It outputs a file that can be imported into systems calendar. Hosted my Vivian Aguirre

In this project you will 
- Understand how to use the Google Gemini API in Python
- Learn prompt engineering and AI→JSON parsing
- Create a smart planner app that turns a project description into a week-by-week plan
- Export the plan into a .csv (for spreadsheets) and .ics (for Apple/Google Calendar)

## Getting Started 
Clone this repository to get the starter code, and use it to follow along the workshop! 

## Create your Google Gemini API Key 
By using this link, it will direct you to the webroswer, and use your personal google account. 

After signing in, go to the sidebar and the dropdown "Dashboard". 
Create a new project by selecting so.

Next, click on "API Keys" on the sidebar, and you will create your personalized (free tier) google api key by clicking on the upper right button. 
When prompted, name your key and choose the project you just created as the imported cloud project. 

python3 -m pip install google-genai==0.3.0 pypdf==4.3.1 python-docx==1.1.2 pillow==10.4.0 pytesseract==0.3.13 toml==0.10.2


python -m venv .venv
source .venv/bin/activate 
pip install -r requirements.txt
