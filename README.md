# DevOpsProject-
CM6453: Distributed AI Software Development Platform

This project is a web-based software system based on a Distributed Multi-Agent Architecture, developed for the CM6453 Software Processes term project.

🚀 About the Project

The main goal of this platform is to build a system of AI agents capable of analyzing long software requirements entered by a user (e.g., "Develop an online library management system") and transforming them into a fully functional web application.

Instead of distributing tasks randomly, the system is managed by a Master Agent. The master agent decomposes the requirements into subtasks, evaluates the capabilities (coding, reasoning, context capacity, etc.) of available agents in the system, and assigns each task to the most suitable LLM-based agent (Frontend, Backend, Database, Testing, Integration).

🛠 Technologies Used

Backend: Python, Django, Django REST Framework (DRF)

Database: SQLite (for the development phase)

Project Management: Trello, Scrum Methodology

Version Control: Git & GitHub

🔄 Scrum and Sprint Plan (10 Weeks)

The project consists of 5 iterations (sprints), each lasting 2 weeks:

Sprint 1: Foundation, requirements, project registration (Storage) system setup, and Master Agent prototype.

Sprint 2: Agent management, model capability evaluation, and task assignment algorithm.

Sprint 3: Distributed development, inter-agent communication, and code generation.

Sprint 4: Integration, automated testing, Code Review agent, and error recovery.

Sprint 5: End-to-end testing, dashboard completion, and final demo.

🌿 Git and Branching Strategy

To prevent code conflicts and maintain a clean history, a Feature Branch workflow is implemented:

Main Branches:

main: Contains only working, tested, and production-ready code.

develop: The active development branch. All completed tasks are first merged here.

Creating a New Branch:

Create a new branch from develop for each Trello card.

Naming convention: feature/[Trello-Card-Code]-[Short-Description]

Example: git checkout -b feature/FR-2-project-creation

Pushing and Merging Code:

When the task is completed, push the code to GitHub.

Open a Pull Request (PR). Once it passes Code Review, merge it into the develop branch.

💻 Setup and Execution

Follow these steps to run the project in your local environment:

Clone the Repository:

git clone [REPO_URL]
cd [REPO_FOLDER]


Create and Activate a Virtual Environment:

python -m venv venv
source venv/bin/activate  # For Mac/Linux
venv\Scripts\activate     # For Windows


Install Required Packages:

pip install -r requirements.txt


Update the Database:

python manage.py makemigrations
python manage.py migrate


Start the Server:

python manage.py runserver


You can view the system by navigating to http://127.0.0.1:8000/ in your browser.
