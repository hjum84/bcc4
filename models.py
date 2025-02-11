# models.py
import os
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Get the database URL from your Render environment (this is like your secret address)
DATABASE_URL = os.getenv("DATABASE_URL")  # Render will give you this value

# Create an engine that knows how to talk to PostgreSQL
engine = create_engine(DATABASE_URL)

# Create a session maker (like a helper to do your database work)
SessionLocal = sessionmaker(bind=engine)

# Create a base class for our models (think of this as the blueprint for our tables)
Base = declarative_base()

# Define the User model (like a table in your notebook)
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    visit_count = Column(Integer, default=0)  # This will count how many times they visit

# Create the table if it doesn't exist yet
Base.metadata.create_all(bind=engine)
