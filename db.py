"""
Database layer for template extraction.
Supports SQLite by default, configurable for PostgreSQL.
Tables: config, template, logs.
"""

import json
from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

Base = declarative_base()

class Config(Base):
    __tablename__ = 'config'
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Template(Base):
    __tablename__ = 'template'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    customer_name = Column(String(100), nullable=False, default="UNKNOWN")
    description = Column(Text)
    json_data = Column(Text, nullable=False)  # full JSON as text (must match original file content)
    signature = Column(Text)  # comma separated or JSON
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Log(Base):
    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String(20), default='INFO')
    action = Column(String(100))
    document_name = Column(String(200))
    template_name = Column(String(100))
    message = Column(Text)
    output_json = Column(Text)  # full result as JSON string
    success = Column(Boolean, default=True)

class Database:
    def __init__(self, db_url=None):
        if db_url is None:
            db_path = Path(__file__).parent / "extraction.db"
            db_url = f"sqlite:///{db_path}"
        
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
    
    def get_session(self):
        return self.Session()
    
    def save_template(self, template_dict: dict, customer_name: str = "UNKNOWN") -> str:
        """Save or update template in DB. json_data must be full original JSON content."""
        session = self.get_session()
        try:
            name = template_dict.get("name", "unknown")
            # Ensure customer_file section is present
            if "customer_file" not in template_dict:
                template_dict = dict(template_dict)  # copy
                template_dict["customer_file"] = customer_name
            json_str = json.dumps(template_dict, indent=2, ensure_ascii=False)
            sig = json.dumps(template_dict.get("signature", []))

            existing = session.query(Template).filter_by(name=name).first()
            if existing:
                existing.json_data = json_str
                existing.signature = sig
                existing.description = template_dict.get("description", "")
                existing.customer_name = customer_name
                existing.updated_at = datetime.utcnow()
            else:
                new_tpl = Template(
                    name=name,
                    customer_name=customer_name,
                    description=template_dict.get("description", ""),
                    json_data=json_str,
                    signature=sig,
                    is_active=True
                )
                session.add(new_tpl)
            
            session.commit()
            return f"DB:template:{name} (customer={customer_name})"
        except SQLAlchemyError as e:
            session.rollback()
            raise RuntimeError(f"DB error saving template: {e}") from e
        finally:
            session.close()
    
    def log_event(self, action: str, document_name: str = "", template_name: str = "", 
                  message: str = "", output_json: dict = None, success: bool = True, level: str = "INFO"):
        """Log everything that happens."""
        session = self.get_session()
        try:
            log_entry = Log(
                level=level,
                action=action,
                document_name=document_name,
                template_name=template_name,
                message=message,
                output_json=json.dumps(output_json, ensure_ascii=False) if output_json else None,
                success=success
            )
            session.add(log_entry)
            session.commit()
        except SQLAlchemyError:
            session.rollback()  # best effort
        finally:
            session.close()
    
    def get_template_by_name(self, name: str) -> dict | None:
        session = self.get_session()
        try:
            tpl = session.query(Template).filter_by(name=name, is_active=True).first()
            if tpl and tpl.json_data:
                return json.loads(tpl.json_data)
            return None
        finally:
            session.close()

# Global instance
db = Database()
